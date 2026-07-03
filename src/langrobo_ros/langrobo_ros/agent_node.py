"""Agent node — thin ROS2 entry point.

Responsibilities (and nothing more):
  1. Declare / read ROS2 parameters
  2. Create ROS2Bridge and wire all subscriptions
  3. Inject bridge + LLM config + services into langrobo_core
  4. Build the LangGraph graph
  5. Run a worker thread that drains the input queue and invokes the graph
  6. Manage conversation history and multimodal user messages

Everything else (graph, agents, tools, services) is langrobo_core — pure
Python, no rclpy — so it runs and tests on any machine.
"""

import logging
import os
import time
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from dotenv import load_dotenv

from langrobo_core.services import config as config_service
from langrobo_core.services import health as health_service
from langrobo_core.services import llm as llm_module
from langrobo_core.services import memory as memory_service
from langrobo_core.services import metrics
from langrobo_core.services.logging import new_trace, setup_logging
from langrobo_core.tools import _bridge as bridge_module
from langrobo_core.tools.reminders import get_store as get_reminder_store
from langrobo_core.graph import build_graph
from langrobo_core.utils import timing
from langrobo_core.utils.history import trim_history
from langrobo_core.utils.speech_stream import SpeechStreamHandler

from .ros2_bridge import ROS2Bridge


class AgentNode(Node):

    def __init__(self):
        super().__init__("agent_node")

        # ── Environment + services config (fail fast on malformed values) ──
        load_dotenv(os.path.expanduser(os.getenv("LANGROBO_ENV_FILE", "~/ros2_ws/.env")))
        config_service.sanitize_tracing_env()
        settings = config_service.load_settings()
        setup_logging(json_format=settings.log_json)

        # ── Parameters ────────────────────────────────────────────────────
        self.declare_parameter("provider",   "llamacpp")
        self.declare_parameter("model",      "default")
        self.declare_parameter("api_key_env", "")
        self.declare_parameter("base_url",   "http://singireddys-mac-mini.local:8080/v1")
        self.declare_parameter("max_tokens", 3000)
        self.declare_parameter("history_turns", 20)
        self.declare_parameter("use_vision",  True)
        # Force the supervisor's mandatory handover via tool_choice (grammar-constrained
        # on llama.cpp). Set False if your llama.cpp build lacks --jinja tool support.
        self.declare_parameter("strict_tool_calls", True)
        # Stream sentence chunks to TTS as the LLM generates (needs a llama.cpp
        # build that streams tool calls). Set False to publish one full reply
        # per turn — the wire protocol (chunks + end marker) stays the same.
        self.declare_parameter("stream_speech", True)
        # llama.cpp KV-cache slot pinning (id_slot). llm_slot pins ALL text agents
        # to one server slot so the shared static prompt prefix stays cached —
        # without it a multi-slot server (--parallel N) scatters sequential
        # requests across cold slots and re-prefills ~2k tokens every turn
        # (~20s on the 12B Mac Mini). -1 disables (e.g. for --parallel 1 servers
        # that reject explicit ids, or cloud providers, which just ignore it).
        self.declare_parameter("llm_slot", 0)
        # Per-agent overrides for the local multimodal agent (Gemma via llama.cpp).
        # local_agent_slot: dedicated KV-cache slot for its image prefix, kept
        # SEPARATE from llm_slot so vision turns never evict the text agents'
        # cached prompt (-1 = none/auto).
        # local_agent_model: override model name for local_agent ("" = inherit global).
        self.declare_parameter("local_agent_slot",  1)
        self.declare_parameter("local_agent_model", "")
        # Slot for the supervisor + specialist agents (navigate/status/swiggy/
        # tracker). Their system prompts differ from chat's, so running them on
        # llm_slot would evict chat's hot prefix — a single navigate turn would
        # make the NEXT chat turn re-prefill ~2k tokens (~20s). A third slot
        # keeps chat's cache untouched across specialist excursions.
        # -1 = share llm_slot (old behaviour).
        self.declare_parameter("specialist_slot", 2)

        provider      = self.get_parameter("provider").value
        model         = self.get_parameter("model").value
        api_key_env   = self.get_parameter("api_key_env").value
        base_url      = self.get_parameter("base_url").value
        max_tokens    = self.get_parameter("max_tokens").value
        # History stores raw message objects (incl. captured image blocks),
        # not turn-pairs, so allow extra room for tool-call / image messages.
        self._max_history    = self.get_parameter("history_turns").value * 4
        self._use_vision     = self.get_parameter("use_vision").value
        llm_slot             = self.get_parameter("llm_slot").value
        local_agent_slot     = self.get_parameter("local_agent_slot").value
        local_agent_model    = self.get_parameter("local_agent_model").value
        specialist_slot      = self.get_parameter("specialist_slot").value
        strict_tool_calls    = self.get_parameter("strict_tool_calls").value
        self._stream_speech  = self.get_parameter("stream_speech").value

        api_key = os.environ.get(api_key_env, "") if api_key_env else "none"
        # The launch file's Mac-Mini base_url default must not poison a cloud
        # provider switch (provider:=openai with the default base_url left in).
        if provider == "openai" and "singireddys-mac-mini" in base_url:
            base_url = ""

        # Per-agent LLM overrides — three-way slot map:
        #   llm_slot (0):        chat, the default responder — its prefix stays
        #                        hot across every specialist excursion.
        #   local_agent_slot(1): local_agent's image prefix, never evicted by
        #                        text agents (and vice versa).
        #   specialist_slot (2): supervisor + navigate/status/swiggy/tracker —
        #                        different system prompts that would otherwise
        #                        thrash chat's slot.
        # The supervisor never emits user-facing text (grammar-forced handover),
        # so it gains nothing from streaming and skips it.
        _spec = {"slot": specialist_slot if specialist_slot >= 0 else None}
        agent_overrides = {
            "local_agent": {
                "slot":  local_agent_slot,
                "model": local_agent_model or None,
            },
            "supervisor": {"streaming": False, **_spec},
            "navigate":   dict(_spec),
            "status":     dict(_spec),
            "swiggy":     dict(_spec),
            "tracker":    dict(_spec),
        }

        # ── Known map locations for Nav2 goal publishing (phase-2 nav slot) ─
        self.declare_parameter("locations.kitchen",     [2.5,  1.0,  0.0])
        self.declare_parameter("locations.living_room", [0.0,  3.0, 90.0])
        self.declare_parameter("locations.bedroom",     [-2.0, 2.0, 180.0])
        self.declare_parameter("locations.entrance",    [0.0,  0.0,  0.0])
        known_locations = {
            name: tuple(self.get_parameter(f"locations.{name}").value)
            for name in ("kitchen", "living_room", "bedroom", "entrance")
        }

        # ── Inject config into the core ───────────────────────────────────
        self._bridge = ROS2Bridge(self, known_locations=known_locations)
        bridge_module.init(self._bridge)
        timing.set_sink(self._bridge.publish_timing)
        self._timing_handler = timing.TimingCallbackHandler()
        llm_module.configure(provider, model, base_url, api_key, max_tokens, agent_overrides,
                             strict_tools=strict_tool_calls,
                             streaming=self._stream_speech,
                             slot=llm_slot if llm_slot >= 0 else None)
        llm_module.configure_fallback(settings.fallback)
        self._memory = memory_service.init(settings.memory)

        # Register navigation completion callback
        self._bridge.register_nav_done_callback(self._on_nav_done)

        # ── Build graph ───────────────────────────────────────────────────
        self._graph   = build_graph()
        self._history: list = []
        self._history_lock = threading.Lock()
        # Background KV-cache warmer (boot + after history trims) — at most one.
        self._warm_thread: threading.Thread | None = None

        # Sticky routing: the agent left active at the end of the previous turn.
        # turn_entry re-enters it directly (skipping the supervisor hop) only if
        # it is STICKY_ELIGIBLE; [SYSTEM] events always force a fresh supervisor route.
        self._sticky_agent: str | None = None
        self._last_turn_ts: float | None = None

        # ── Input queue ───────────────────────────────────────────────────
        # _user_pending:   latest user message (replaced by newer ones)
        # _system_pending: FIFO of system events (delivery, nav done, reminder
        #                  due) — never dropped, never clobber each other
        # _input_event:    wakes the worker when anything is pending
        self._queue_lock     = threading.Lock()
        self._user_pending:   str | None = None
        self._system_pending: list[str] = []
        self._input_event    = threading.Event()

        # ── Startup readiness gate ────────────────────────────────────────
        self._ready_event = threading.Event()

        # ── Publishers ────────────────────────────────────────────────────
        self._pub_thinking = self.create_publisher(Bool, "/brain/thinking", 1)

        # ── Subscribers ───────────────────────────────────────────────────
        self.create_subscription(String, "/voice/user_input", self._on_user_input, 10)
        # Stop keyword spotted by the Jetson while TTS plays: the Jetson halts
        # speech itself; here we make it a safety word — halt wheels too.
        self.create_subscription(String, "/voice/tts_stop", self._on_tts_stop, 10)

        if self._use_vision:
            from sensor_msgs.msg import CompressedImage
            # Consume the JPEG that camera_node already publishes — no raw-frame
            # transport over the Jetson↔Pi5 link and no re-encode on the Pi5.
            # The compressed bytes ARE what look() needs (base64 image/jpeg).
            self.create_subscription(CompressedImage, "/camera/color/image_raw/compressed",
                                     self._on_compressed_image, 1)
            self.create_subscription(String, "/vision/target_result",    self._bridge.on_target_result, 10)
            self.get_logger().info(
                "Vision enabled — /camera/color/image_raw/compressed + /vision/target_result")

        # ── Health/status/metrics API (in-process, daemon thread) ─────────
        health_service.start_health_api(settings.health, extra_status=self._runtime_status)

        # ── Worker + startup threads ──────────────────────────────────────
        threading.Thread(target=self._startup_check, daemon=True).start()
        threading.Thread(target=self._worker_loop,   daemon=True).start()

        # ── Delivery polling timer (every 2 min) ──────────────────────────
        self.create_timer(120.0, self._poll_delivery)

        # ── Reminder polling timer (every 5 s → proactive speech) ─────────
        self.create_timer(5.0, self._poll_reminders)

        self.get_logger().info(
            f"Agent starting — provider: {provider}, base_url: {base_url}, "
            f"vision: {self._use_vision}"
        )

    # ── Health API status hook (any thread) ───────────────────────────────

    def _runtime_status(self) -> dict:
        with self._queue_lock:
            queued_system = len(self._system_pending)
            user_pending = self._user_pending is not None
        return {
            "sticky_agent": self._sticky_agent,
            "last_turn_ts": self._last_turn_ts,
            "camera_frame_age_s": self._bridge.frame_age(),
            "active_order": self._bridge.get_active_order(),
            "queued_system_events": queued_system,
            "user_input_pending": user_pending,
        }

    # ── Startup readiness check ───────────────────────────────────────────

    def _startup_check(self) -> None:
        """Announce readiness. Nav2 check skipped — not available in current hardware scope."""
        self._bridge.publish_speech("I'm ready.")
        self.get_logger().info("Startup complete")
        self._ready_event.set()
        # Prefill chat's llama.cpp slot with the ~2k-token static prompt now,
        # so the first user turn of the session doesn't pay the ~20s prefill.
        self._start_cache_warm()

    # ── Navigation done callback (background nav thread → worker) ─────────

    def _on_nav_done(self, success: bool, message: str) -> None:
        """Called by bridge when Nav2 goal finishes. Injects system message."""
        status = "Navigation succeeded" if success else "Navigation failed"
        self._enqueue_system(f"[SYSTEM] {status}: {message}")

    # ── Image callback (spin thread) ──────────────────────────────────────

    def _on_compressed_image(self, msg) -> None:
        # msg.data is already JPEG (camera_node encodes '.jpg', format='jpeg').
        try:
            self._bridge.on_image(bytes(msg.data))
        except Exception as e:
            self.get_logger().warning(f"Frame cache error: {e}")

    # ── Two-slot queue (spin thread → worker thread) ──────────────────────

    def _on_user_input(self, msg: String) -> None:
        text = msg.data.strip()
        if not text:
            return
        timing.emit("brain_receive", chars=len(text))
        # New user input cancels any active navigation AND interrupts any blocking
        # motion tool (visual servoing / timed drive), then replaces pending input.
        self._bridge.cancel_navigation()
        self._bridge.request_motion_stop()
        with self._queue_lock:
            if self._user_pending is not None:
                self.get_logger().warning("User queue: replacing pending message with newer input")
            self._user_pending = text
        self._input_event.set()

    def _on_tts_stop(self, msg: String) -> None:
        """Stop keyword heard during robot speech — halt any motion as well."""
        self.get_logger().info(f'Stop keyword ("{msg.data}") — cancelling motion')
        self._bridge.cancel_navigation()
        self._bridge.request_motion_stop()

    def _enqueue_system(self, text: str) -> None:
        """Enqueue a system event — never dropped, fires after current graph run."""
        with self._queue_lock:
            self._system_pending.append(text)
        self._input_event.set()

    def _poll_delivery(self) -> None:
        """Timer callback — injects a delivery check if an order is active."""
        order_id = self._bridge.get_active_order()
        if order_id:
            self._enqueue_system(f"[SYSTEM] Check if order {order_id} has been delivered")

    def _poll_reminders(self) -> None:
        """Timer callback — fires due reminders as a proactive-speech turn."""
        due = get_reminder_store().pop_due()
        if not due:
            return
        lines = "; ".join(f'"{r.text}" (set for {self._fmt_clock(r.due)})' for r in due)
        self.get_logger().info(f"Reminder(s) due: {lines}")
        self._enqueue_system(
            f"[SYSTEM] Reminder due — announce to the user now: {lines}")

    @staticmethod
    def _fmt_clock(t: float) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(t).strftime("%I:%M %p").lstrip("0")

    # ── Worker loop ───────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        self._ready_event.wait()  # wait for startup check to complete

        while True:
            self._input_event.wait()

            # Drain ONE item per iteration — system events take priority, but a
            # user message that arrived in the same window is NOT discarded: we
            # leave it pending and keep the event armed for the next loop.
            with self._queue_lock:
                if self._system_pending:
                    text, is_system = self._system_pending.pop(0), True
                    if not self._system_pending and self._user_pending is None:
                        self._input_event.clear()
                    # else: leave event set so remaining input is processed next
                else:
                    text, is_system = self._user_pending, False
                    self._user_pending = None
                    self._input_event.clear()

            if text:
                self._process(text, is_system)

    # ── Graph invocation (worker thread) ──────────────────────────────────

    def _process(self, text: str, is_system: bool = False) -> None:
        from langchain_core.messages import HumanMessage
        trace = new_trace()   # stamps every log line + timing event this turn
        turn_start = time.time()
        metrics.inc("turns_total")
        if is_system:
            metrics.inc("system_turns_total")
        self._pub_thinking.publish(Bool(data=True))
        try:
            with self._history_lock:
                history = list(self._history)

            # Plain-text user turn. Camera frames enter the conversation only when
            # local_agent calls look() — no ambient frame-stapling. Per-agent
            # projection (langrobo_core.utils.message_utils) strips images for
            # text agents.
            messages = history + [HumanMessage(content=text)]

            # Sticky routing: re-enter the previous agent for a user follow-up;
            # [SYSTEM] events always get a fresh supervisor route. turn_entry
            # enforces which agents are actually sticky-eligible and defaults
            # everything else to chat (the single-call common path).
            incoming_agent = "supervisor" if is_system else (self._sticky_agent or "chat")

            self.get_logger().info(
                f"Invoking graph with input: {text} (entry={incoming_agent}, trace={trace})")
            timing.emit("graph_start", entry=incoming_agent, system=is_system, trace=trace)

            # Fresh handler per turn: streams sentence chunks to TTS while the
            # LLM generates. Pre-tool text ("Let me check.") is spoken as the
            # tool runs; the turn's utterance is closed with the EOU marker below.
            speech_stream = (
                SpeechStreamHandler(self._bridge.publish_speech_chunk)
                if self._stream_speech else None
            )
            callbacks = [self._timing_handler] + ([speech_stream] if speech_stream else [])

            result = None
            for event in self._graph.stream(
                {"messages": messages, "active_agent": incoming_agent},
                config={"callbacks": callbacks},
                stream_mode="values"):
                if "messages" in event:
                    msg = event["messages"][-1]
                    self.get_logger().info(f"Step message [{type(msg).__name__}]: {str(msg.content)[:200]} (tool_calls: {getattr(msg, 'tool_calls', None)})")
                result = event

            timing.emit("graph_end")

            # Remember where the turn ended so the next user follow-up can skip
            # the supervisor (turn_entry gates which agents are sticky-eligible).
            self._sticky_agent = result.get("active_agent") if result else None

            response = self._extract_response(result)

            if not response:
                if speech_stream and speech_stream.chunks_sent:
                    # Something was streamed but the graph ended without a final
                    # text — close the utterance so the Jetson releases the mic.
                    self._bridge.publish_speech_end()
                self.get_logger().warning("Graph returned empty response")
                metrics.inc("empty_responses_total")
                return

            # Persist the FULL message list from the graph (including any frames
            # captured via look()), so follow-up turns reason over the same image.
            # Trim at a turn boundary so the cached image prefix stays intact until
            # a deliberate reset (never a per-turn front shift).
            new_history = result.get("messages", messages) if result else messages
            with self._history_lock:
                self._history, history_reset = trim_history(new_history, self._max_history)

            self.get_logger().info(f"→ TTS: {response[:120]}")
            if speech_stream and speech_stream.spoke(response):
                # Final text already went out sentence-by-sentence — just close
                # the utterance. (Any pre-tool acks streamed earlier are part of
                # the same utterance.)
                timing.emit("speech_stream_done", chunks=speech_stream.chunks_sent)
                self._bridge.publish_speech_end()
            else:
                # Streaming off, or the response never streamed (safe_invoke
                # fallback, non-streaming provider) — speak it whole. This also
                # closes any partial stream with the trailing EOU marker.
                self._bridge.publish_speech(response)

            # Long-term episodic memory: user turns only ([SYSTEM] events are
            # plumbing). Non-blocking — embedding happens on the memory
            # service's writer thread, never on the turn path.
            if not is_system:
                self._memory.record_turn(text, response,
                                         agent=self._sticky_agent or "chat")

            if history_reset:
                # The trim shifted the prompt prefix — every slot serving this
                # history is now cold. Re-prefill in the background while the
                # robot is idle, so the NEXT turn doesn't pay ~20s+ up front.
                self._start_cache_warm()

        except Exception as e:
            self.get_logger().error(f"Graph error: {e}")
            metrics.inc("turn_errors_total")
            self._bridge.publish_speech("I'm having trouble right now. Please try again in a moment.")
        finally:
            self._last_turn_ts = time.time()
            metrics.set_gauge("last_turn_duration_seconds",
                              round(time.time() - turn_start, 3))
            self._pub_thinking.publish(Bool(data=False))

    # ── KV-cache warming (background) ─────────────────────────────────────

    def _start_cache_warm(self) -> None:
        """Kick off one background prefill of the next-turn prompt (no-op if
        one is already running)."""
        if self._warm_thread and self._warm_thread.is_alive():
            return
        # Warm the agent the next turn will actually enter: the sticky
        # specialist if one is active (local_agent keeps its image prefix on
        # its own slot), otherwise chat — the default responder.
        agent = self._sticky_agent if self._sticky_agent == "local_agent" else "chat"
        self._warm_thread = threading.Thread(
            target=self._warm_cache, args=(agent,), daemon=True)
        self._warm_thread.start()

    def _warm_cache(self, agent: str) -> None:
        """Prefill `agent`'s llama.cpp slot with its current projected prompt.

        Sends the IDENTICAL system prompt + bound tools + history the next real
        turn will send (via the agent's build_llm_call), with max_tokens=1, so
        the server caches the prefix while the robot is idle. If the user
        speaks mid-warm, the real request queues behind this one on the same
        slot and then reuses the very prefix being computed — total prefill
        work is the same, so the race is harmless.
        """
        try:
            from langchain_core.messages import HumanMessage
            if self._input_event.is_set():
                return  # a turn is already pending — it will pay the prefill itself
            with self._history_lock:
                history = list(self._history)
            if agent == "local_agent":
                from langrobo_core.agents.local_agent import build_llm_call
            else:
                from langrobo_core.agents.chat import build_llm_call
            # The trailing "(warmup)" user message only diverges at the tail —
            # everything before it (the expensive part) is cached for real turns.
            llm, msgs = build_llm_call(history + [HumanMessage(content="(warmup)")])
            llm.bind(max_tokens=1).invoke(msgs)
            self.get_logger().info(
                f"KV-cache warmed for '{agent}' ({len(history)} history messages)")
        except Exception as e:
            self.get_logger().warning(f"KV-cache warm failed (non-fatal): {e}")

    def _extract_response(self, result: dict) -> str | None:
        """Return the last non-empty AI text from the graph output."""
        from langchain_core.messages import AIMessage, ToolMessage
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, ToolMessage):
                continue
            if isinstance(msg, AIMessage):
                content = msg.content
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    text = " ".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ).strip()
                    if text:
                        return text
        return None


def main(args=None):
    rclpy.init(args=args)
    node = AgentNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
