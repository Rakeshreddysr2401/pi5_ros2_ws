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

import json
import logging
import os
import time
import threading
from contextlib import contextmanager

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from dotenv import load_dotenv

from langrobo_core.services import briefing as briefing_service
from langrobo_core.services import config as config_service
from langrobo_core.services import consolidation as consolidation_service
from langrobo_core.services import health as health_service
from langrobo_core.services import llm as llm_module
from langrobo_core.services import memory as memory_service
from langrobo_core.services import metrics
from langrobo_core.services import telegram as telegram_service
from langrobo_core.services import watch as watch_service
from langrobo_core.services import world_model as world_model_service
from langrobo_core.services.logging import new_trace, setup_logging
from langrobo_core.tools import _bridge as bridge_module
from langrobo_core.tools.errands import get_store as get_errand_store
from langrobo_core.tools.reminders import get_store as get_reminder_store
from langrobo_core.graph import build_graph
from langrobo_core.utils import timing
from langrobo_core.utils.history import trim_history
from langrobo_core.utils.utterance import join_utterances, looks_incomplete
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
        # A natural mid-thought pause outlasts the VAD's 600ms end-silence, so
        # one sentence arrives as two utterances. Within this window a second
        # utterance CONTINUES the first instead of replacing it — replacing
        # silently discarded the first half ("I want you to" + "go to the
        # kitchen" reached the graph as just "go to the kitchen"). 0 disables.
        self.declare_parameter("utterance_merge_window_s", 1.2)
        self.declare_parameter("use_vision",  True)
        # Force the supervisor's mandatory handover via tool_choice (grammar-constrained
        # on llama.cpp). Set False if your llama.cpp build lacks --jinja tool support.
        self.declare_parameter("strict_tool_calls", True)
        # Stream sentence chunks to TTS as the LLM generates (needs a llama.cpp
        # build that streams tool calls). Set False to publish one full reply
        # per turn — the wire protocol (chunks + end marker) stays the same.
        self.declare_parameter("stream_speech", True)
        # Deterministic movement fast-path: exact spoken movement commands
        # ("stop", "go to the kitchen", "come here", "forward 30") execute
        # directly — zero LLM calls, sub-100ms command-to-motion. Anything the
        # matcher isn't sure about falls through to the normal graph.
        self.declare_parameter("fast_path", True)
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
        # Slot for the specialist agents (navigate/status/swiggy/tracker/
        # knowledge/briefing). Their system prompts differ from chat's, so
        # running them on llm_slot would evict chat's hot prefix — a single
        # navigate turn would make the NEXT chat turn re-prefill ~2k tokens
        # (~20s). A third slot keeps chat's cache untouched across specialist
        # excursions. -1 = share llm_slot (old behaviour).
        self.declare_parameter("specialist_slot", 2)
        # Slot for the supervisor ALONE. It runs on every [SYSTEM] turn
        # (reminders, watch alerts, briefings); sharing the specialist slot
        # meant each routed a full-history re-prefill onto the other
        # (~18-50s, measured 2026-07-06). -1 = share specialist_slot.
        self.declare_parameter("supervisor_slot", 3)
        # Slot for navigate ALONE. It's the most latency-sensitive specialist
        # (real robot/sim movement) and previously shared specialist_slot with
        # status/swiggy/tracker/knowledge/briefing — any of those running
        # first would evict navigate's cache before a movement command.
        # -1 = share specialist_slot (old behaviour).
        self.declare_parameter("navigate_slot", 4)

        provider      = self.get_parameter("provider").value
        model         = self.get_parameter("model").value
        api_key_env   = self.get_parameter("api_key_env").value
        base_url      = self.get_parameter("base_url").value
        max_tokens    = self.get_parameter("max_tokens").value
        # History stores raw message objects (incl. captured image blocks),
        # not turn-pairs, so allow extra room for tool-call / image messages.
        self._max_history    = self.get_parameter("history_turns").value * 4
        self._merge_window   = float(self.get_parameter("utterance_merge_window_s").value)
        self._use_vision     = self.get_parameter("use_vision").value
        llm_slot             = self.get_parameter("llm_slot").value
        local_agent_slot     = self.get_parameter("local_agent_slot").value
        local_agent_model    = self.get_parameter("local_agent_model").value
        specialist_slot      = self.get_parameter("specialist_slot").value
        supervisor_slot      = self.get_parameter("supervisor_slot").value
        navigate_slot        = self.get_parameter("navigate_slot").value
        strict_tool_calls    = self.get_parameter("strict_tool_calls").value
        self._stream_speech  = self.get_parameter("stream_speech").value
        self._fast_path      = self.get_parameter("fast_path").value

        api_key = os.environ.get(api_key_env, "") if api_key_env else "none"
        # The launch file's Mac-Mini base_url default must not poison a cloud
        # provider switch (provider:=openai with the default base_url left in).
        if provider == "openai" and "singireddys-mac-mini" in base_url:
            base_url = ""

        # ── Adaptive slot map: fold pins the server can't honour ────────────
        # The configured map assumes --parallel 5, but the Mac Mini sometimes
        # runs fewer slots (memory pressure). Probe the server's real slot
        # count and fold the LEAST-frequent agents first onto slots that
        # already share (navigate → specialist → chat's slot), keeping chat,
        # local_agent and supervisor on their own slots as long as possible.
        # Probe unreachable → keep the configured map (server may boot later).
        if provider == "llamacpp":
            total_slots = self._probe_total_slots(base_url)
            if total_slots:
                def _fold(slot: int, fallback: int, name: str) -> int:
                    if slot is not None and slot >= total_slots:   # -1 never folds
                        self.get_logger().warning(
                            f"{name}_slot {slot} is out of range for this "
                            f"server ({total_slots} slots) — sharing slot "
                            f"{fallback} instead")
                        return fallback
                    return slot
                specialist_slot = _fold(specialist_slot, llm_slot, "specialist")
                supervisor_slot = _fold(supervisor_slot, specialist_slot, "supervisor")
                navigate_slot   = _fold(navigate_slot,   specialist_slot, "navigate")
                local_agent_slot = _fold(local_agent_slot, llm_slot, "local_agent")

        # Per-agent LLM overrides — slot map:
        #   llm_slot (0):        chat, the default responder — its prefix stays
        #                        hot across every specialist excursion.
        #   local_agent_slot(1): local_agent's image prefix, never evicted by
        #                        text agents (and vice versa).
        #   specialist_slot (2): status/swiggy/instamart/dineout/tracker/
        #                        knowledge/briefing — different system prompts
        #                        that would otherwise thrash chat's slot.
        #   supervisor_slot (3): supervisor alone (every [SYSTEM] turn).
        #   navigate_slot (4):   navigate alone (real movement — most
        #                        latency-sensitive specialist).
        # The supervisor never emits user-facing text (grammar-forced handover),
        # so it gains nothing from streaming and skips it.
        _spec = {"slot": specialist_slot if specialist_slot >= 0 else None}
        # Supervisor gets its OWN slot (falls back to the specialist slot when
        # unset): it fires on every [SYSTEM] turn and must not evict — or be
        # evicted by — whichever specialist is cached on slot 2.
        _sup = {"slot": supervisor_slot if supervisor_slot >= 0 else _spec["slot"]}
        # Navigate gets its OWN slot too (falls back to the specialist slot
        # when unset) — real movement commands shouldn't wait behind whichever
        # other specialist last evicted slot 2.
        _nav = {"slot": navigate_slot if navigate_slot >= 0 else _spec["slot"]}
        agent_overrides = {
            "local_agent": {
                "slot":  local_agent_slot,
                "model": local_agent_model or None,
            },
            "supervisor": {"streaming": False, **_sup},
            "navigate":   dict(_nav),
            "status":     dict(_spec),
            "swiggy":     dict(_spec),
            "instamart":  dict(_spec),
            "dineout":    dict(_spec),
            "tracker":    dict(_spec),
            "knowledge":  dict(_spec),
            "briefing":   dict(_spec),
            # Nightly memory consolidation is a background batch job — it must
            # never stream and never touch chat's slot (a 3am run would evict
            # the hot prefix and make the first morning turn pay ~20s prefill).
            "consolidation": {"streaming": False, **_spec},
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

        # ── Robot body: real rover (ESP32) vs Gazebo sim (rover_sim) ────────
        # Only affects the cmd_vel wire shape/topic in ROS2Bridge — see
        # CLAUDE.md "Simulation laptop" and scripts/fleet.sh {sim|rover}.
        self.declare_parameter("robot_body", "rover")
        robot_body = self.get_parameter("robot_body").value
        if robot_body not in ("rover", "sim"):
            self.get_logger().warning(
                f"Unknown robot_body {robot_body!r} — defaulting to 'rover'")
            robot_body = "rover"

        # ── Inject config into the core ───────────────────────────────────
        self._bridge = ROS2Bridge(self, known_locations=known_locations,
                                  robot_body=robot_body, use_vision=self._use_vision)
        bridge_module.init(self._bridge)
        timing.set_sink(self._bridge.publish_timing)
        self._timing_handler = timing.TimingCallbackHandler()
        llm_module.configure(provider, model, base_url, api_key, max_tokens, agent_overrides,
                             strict_tools=strict_tool_calls,
                             streaming=self._stream_speech,
                             slot=llm_slot if llm_slot >= 0 else None)
        llm_module.configure_fallback(settings.fallback)
        self._memory = memory_service.init(settings.memory)
        self._telegram = telegram_service.init(settings.telegram)
        self._watch = watch_service.init(settings.watch)
        self._world = world_model_service.init(settings.world_model)
        self._consolidator = consolidation_service.init(settings.consolidation, self._memory)
        self._briefing = briefing_service.init(settings.briefing)

        # Register navigation completion callback
        self._bridge.register_nav_done_callback(self._on_nav_done)
        # Let pure-zone tools inject [SYSTEM] turns (announce_at_home et al.)
        self._bridge.register_system_turn_callback(self._enqueue_system)

        # ── Build graph ───────────────────────────────────────────────────
        self._graph   = build_graph()
        self._history: list = []
        self._history_lock = threading.Lock()
        # Background KV-cache warmer (boot + after history trims) — at most one.
        self._warm_thread: threading.Thread | None = None
        # Supervisor runs on its own pinned slot for every [SYSTEM] turn
        # (reminders/watch alerts/briefing) — warmed independently of
        # whichever specialist/chat the warm above targets, since it's a
        # separate slot with a separate prompt.
        self._sup_warm_thread: threading.Thread | None = None

        # Sticky routing: the agent left active at the end of the previous turn.
        # turn_entry re-enters it directly (skipping the supervisor hop) only if
        # it is STICKY_ELIGIBLE; [SYSTEM] events always force a fresh supervisor route.
        self._sticky_agent: str | None = None
        # Last /voice/stt_meta, with the wall clock it arrived — stapled onto the
        # next voice turn's trace. Stale ones are dropped: a Telegram turn (or a
        # turn minutes later) must not inherit an old utterance's STT cost.
        self._stt_meta: tuple[float, dict] | None = None
        # The trace id of the turn in flight, readable from the ROS spin thread
        # (new_trace()'s contextvar is worker-thread-local, so /voice/tts_meta
        # arriving on the spin thread cannot see it).
        self._trace_for_tts: str | None = None
        self._last_turn_ts: float | None = None

        # ── Input queue ───────────────────────────────────────────────────
        # _user_pending:     latest user message (replaced by newer ones)
        # _system_pending:   FIFO of system events (delivery, nav done, reminder
        #                    due) — never dropped, never clobber each other
        # _telegram_pending: FIFO of TelegramInbound — never dropped; drained
        #                    after voice (the person in the room comes first)
        # _input_event:      wakes the worker when anything is pending
        self._queue_lock     = threading.Lock()
        self._user_pending:   str | None = None
        self._user_pending_at: float = 0.0
        self._system_pending: list[str] = []
        self._telegram_pending: list = []
        self._input_event    = threading.Event()
        # Barge-in: set when a NEW user utterance arrives while a turn is
        # still running — the worker aborts the in-flight turn at the next
        # graph step so the new utterance is answered instead of the stale one.
        # (The Jetson forwards mid-TTS speech once AEC lands — see
        # JETSON_VOICE_UPGRADE.md; without AEC this fires only on queued input.)
        self._turn_interrupt = threading.Event()
        self._turn_active    = False
        # Channel of the in-flight turn. Barge-in only applies to voice turns —
        # a spoken answer goes stale when the user speaks over it, but a
        # Telegram reply doesn't; new voice input queues behind it instead.
        self._turn_channel   = "voice"

        # ── Startup readiness gate ────────────────────────────────────────
        self._ready_event = threading.Event()

        # ── Publishers ────────────────────────────────────────────────────
        self._pub_thinking = self.create_publisher(Bool, "/brain/thinking", 1)

        # ── Subscribers ───────────────────────────────────────────────────
        self.create_subscription(String, "/voice/user_input", self._on_user_input, 10)
        # Stop keyword spotted by the Jetson while TTS plays: the Jetson halts
        # speech itself; here we make it a safety word — halt wheels too.
        self.create_subscription(String, "/voice/tts_stop", self._on_tts_stop, 10)
        # Voice-pipeline cost telemetry from pi5_voice_pkg (input and output
        # legs). Purely observational — nothing in the turn path depends on
        # these arriving, so a voice stack without them changes nothing.
        self.create_subscription(String, "/voice/stt_meta", self._on_stt_meta, 10)
        self.create_subscription(String, "/voice/tts_meta", self._on_tts_meta, 10)

        # Vision inputs (camera frame, target result, 3D detections) are
        # subscribed by ROS2Bridge itself — it owns the caches they fill, so
        # every owner of a bridge gets them, not just this node. See the
        # "Vision inputs" block in ros2_bridge.py.
        if self._use_vision:
            self.get_logger().info(
                "Vision enabled — /camera/color/image_raw/compressed + /vision/target_result")

        # ── Health/status/metrics API (in-process, daemon thread) ─────────
        health_service.start_health_api(settings.health, extra_status=self._runtime_status)

        # ── Worker + startup threads ──────────────────────────────────────
        threading.Thread(target=self._startup_check, daemon=True).start()
        threading.Thread(target=self._worker_loop,   daemon=True).start()

        # ── Telegram inbound (long-poll daemon → worker queue) ────────────
        # No-op when the channel is unconfigured. Messages sent while the
        # brain was down arrive now (persisted getUpdates offset).
        self._telegram.start_polling(self._on_telegram_inbound)

        # ── Delivery polling timer (every 2 min) ──────────────────────────
        self.create_timer(120.0, self._poll_delivery)

        # ── Reminder polling timer (every 5 s → proactive speech) ─────────
        self.create_timer(5.0, self._poll_reminders)

        # ── Home watch poll (every 2 s while armed → photo alert) ─────────
        # Keeps the Jetson target finder hunting "person" while armed and
        # turns confident detections into alerts (services/watch.py).
        self._watch_target_set = False
        self.create_timer(2.0, self._poll_watch)

        # ── Memory consolidation check (every 60 s; runs ≤ once/day) ──────
        self.create_timer(60.0, self._poll_consolidation)

        # ── Morning briefing check (every 60 s; fires ≤ once/day) ─────────
        self.create_timer(60.0, self._poll_briefing)

        self.get_logger().info(
            f"Agent starting — provider: {provider}, base_url: {base_url}, "
            f"vision: {self._use_vision}"
        )

    # ── Health API status hook (any thread) ───────────────────────────────

    def _runtime_status(self) -> dict:
        with self._queue_lock:
            queued_system = len(self._system_pending)
            queued_telegram = len(self._telegram_pending)
            user_pending = self._user_pending is not None
        return {
            "sticky_agent": self._sticky_agent,
            "robot_body": self._bridge.robot_body,
            "last_turn_ts": self._last_turn_ts,
            "camera_frame_age_s": self._bridge.frame_age(),
            "active_order": self._bridge.get_active_order(),
            "queued_system_events": queued_system,
            "queued_telegram_messages": queued_telegram,
            "user_input_pending": user_pending,
            "telegram": self._telegram.status(),
            "watch": self._watch.status(),
            "consolidation": self._consolidator.status(),
            "briefing": self._briefing.status(),
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
        """Called by bridge when Nav2 goal finishes. Injects system message.

        A system turn replies to the speaker by default; when the navigation
        was requested over Telegram the report belongs in that chat, so the
        turn carries an explicit routing instruction (same pattern as the
        errand-reply forwarding in _frame_telegram_turn)."""
        status = "Navigation succeeded" if success else "Navigation failed"
        from langrobo_core.tools.movement import get_last_nav_requester
        req = get_last_nav_requester() or {}
        routing = ""
        if req.get("channel") == "telegram":
            routing = (f" (This navigation was requested by {req.get('sender')} "
                       f"over Telegram — send this report to them with "
                       f"send_telegram_message instead of saying it aloud.)")
        self._enqueue_system(f"[SYSTEM] {status}: {message}{routing}")

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
            now = time.monotonic()
            if (self._user_pending is not None and self._merge_window > 0
                    and now - self._user_pending_at <= self._merge_window):
                # Continuation of an utterance we have not answered yet.
                self.get_logger().info("User queue: merging continued utterance")
                self._user_pending = f"{self._user_pending} {text}"
            else:
                if self._user_pending is not None:
                    self.get_logger().warning("User queue: replacing pending message with newer input")
                self._user_pending = text
            self._user_pending_at = now
            if self._turn_active and self._turn_channel == "voice":
                # Barge-in: abandon the in-flight turn — the user has moved on.
                # Telegram turns are never abandoned; this input queues behind.
                self._turn_interrupt.set()
        self._input_event.set()

    def _on_tts_stop(self, msg: String) -> None:
        """Stop keyword heard during robot speech — halt motion AND music.

        The Jetson already halts its own TTS playback (and stops music locally
        for instant response); this is the brain-side sweep so nothing keeps
        moving or playing if the Jetson-local path missed it.

        Wake-word barge-in rides the same topic tagged "[wake:…]": it only
        halts TTS on the Jetson — music keeps playing (AEC subtracts it) and
        the user's new utterance does its own motion sweep on arrival — so it
        must NOT trigger the stop-everything sweep here."""
        if msg.data.startswith("[wake:"):
            return
        self.get_logger().info(f'Stop keyword ("{msg.data}") — cancelling motion + music')
        self._bridge.cancel_navigation()
        self._bridge.request_motion_stop()
        self._bridge.music_command({"action": "stop", "t": time.time()})

    # ── Voice-pipeline telemetry (spin thread) ────────────────────────────

    STT_META_MAX_AGE_S = 30.0   # older than this and it belongs to another utterance

    def _on_stt_meta(self, msg: String) -> None:
        """Cache the STT leg's cost for the voice turn that is about to arrive.

        stt_node publishes this immediately before /voice/user_input, so the
        turn that follows is (barring a dropped message) the one it describes.
        """
        try:
            self._stt_meta = (time.time(), json.loads(msg.data))
        except Exception:
            self.get_logger().debug("bad /voice/stt_meta payload")

    def _on_tts_meta(self, msg: String) -> None:
        """Log one synthesised sentence to LangSmith as its own run.

        Not a child of the turn's graph run: sentences are synthesised while
        the graph is still streaming (and the last ones after it ends), and
        graph.stream() gives us no run tree to hang them off. They carry the
        turn's trace_id instead, so filtering the LangSmith UI on that one id
        shows the LLM run and every sentence it spoke together.
        """
        try:
            meta = json.loads(msg.data)
        except Exception:
            self.get_logger().debug("bad /voice/tts_meta payload")
            return
        trace = self._trace_for_tts
        with self._trace_span(
                f"tts:{meta.get('provider', 'unknown')}",
                inputs={"chars": meta.get("chars")},
                metadata={"trace_id": trace, "channel": "voice", **meta},
                tags=["channel:voice", "leg:tts"]) as span:
            if span is not None:
                span.end(outputs={k: meta.get(k) for k in
                                  ("ok", "latency_ms", "audio_ms", "rtf", "fell_back")})

    def _enqueue_system(self, text: str) -> None:
        """Enqueue a system event — never dropped, fires after current graph run."""
        with self._queue_lock:
            self._system_pending.append(text)
        self._input_event.set()

    def _on_telegram_inbound(self, inbound) -> None:
        """Telegram poller thread → worker queue. Never barges in on a running
        turn and never clobbers voice input — it waits its turn in FIFO order."""
        with self._queue_lock:
            self._telegram_pending.append(inbound)
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
        # The Telegram relay is a blocking HTTP POST (15s timeout) — like the
        # watch alert, it runs on a short-lived background thread, never on
        # the spin thread (which must keep servicing voice/camera/stop).
        relays = [r for r in due if r.telegram_recipient]
        if relays:
            threading.Thread(target=self._relay_reminders, args=(relays,),
                             daemon=True).start()

        def _line(r):
            s = f'"{r.text}" (set for {self._fmt_clock(r.due)})'
            if r.telegram_recipient:
                s += f" — also being relayed to {r.telegram_recipient} on Telegram"
            return s

        lines = "; ".join(_line(r) for r in due)
        self.get_logger().info(f"Reminder(s) due: {lines}")
        self._enqueue_system(
            f"[SYSTEM] Reminder due — announce to the user now: {lines}")

    def _relay_reminders(self, relays: list) -> None:
        """Deterministic Telegram relay for reminders created with
        telegram_recipient (background thread) — goes through
        send_telegram_message's own permission/quiet-hours/D10 gates
        (channel='system' already bypasses the ask-back gate, matching
        announce_at_home/errand semantics), so this doesn't bypass anything
        a normal call wouldn't."""
        from langrobo_core.tools.telegram import send_telegram_message
        for r in relays:
            try:
                result = send_telegram_message.invoke({
                    "recipient": r.telegram_recipient, "message": r.text,
                    "report_back": r.telegram_report_back,
                    "state": {"channel": "system", "messages": []},
                })
                self.get_logger().info(f"Reminder #{r.id} Telegram relay: {result}")
            except Exception as e:
                self.get_logger().warning(f"Reminder #{r.id} Telegram relay failed: {e}")

    @staticmethod
    def _fmt_clock(t: float) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(t).strftime("%I:%M %p").lstrip("0")

    def _poll_watch(self) -> None:
        """Timer callback (spin thread) — the home-watch detection loop.

        While armed: keep the Jetson target finder hunting "person" and turn a
        confident detection into an alert. All I/O that can block (Telegram
        send) happens on a short-lived background thread, never here."""
        if not self._watch.armed():
            if self._watch_target_set:
                # We set the hunt; idle it. (A servo tool that set its own
                # target clears it in its finally block — not our flag.)
                self._bridge.set_vision_target("")
                self._watch_target_set = False
            return
        with self._queue_lock:
            if self._turn_active:
                return   # never fight an in-turn servo loop or alert mid-conversation
        result = self._bridge.get_target_result(max_age_s=3.0)
        if result is None or result.get("target") != "person":
            # Nothing fresh, or a finished servo left a different target —
            # (re)assert the person hunt. Also recovers from Jetson restarts.
            # NB: set_vision_target drops the cached result, so assert-then-
            # return and read on the next tick.
            self._bridge.set_vision_target("person")
            self._watch_target_set = True
            return
        self._watch_target_set = True
        if self._watch.should_alert(bool(result.get("found")),
                                    float(result.get("conf", 0.0))):
            frame = self._bridge.get_frame(max_age_s=10.0)
            threading.Thread(target=self._send_watch_alert, args=(frame,),
                             daemon=True, name="watch_alert").start()

    def _send_watch_alert(self, frame) -> None:
        """Background thread: photo to owners' phones (deterministic — works
        with the LLM down), then a [SYSTEM] turn for the spoken announcement."""
        reached = self._watch.send_alert(frame)
        delivered = (f"A photo was already sent to {', '.join(reached)} on Telegram"
                     if reached else
                     "The phone alert could NOT be delivered")
        self._enqueue_system(
            f"[SYSTEM] Watch alert — watch mode is armed and a person was just "
            f"seen by the camera. {delivered}. Announce aloud briefly that you "
            f"noticed someone and notified the owner.")

    def _poll_consolidation(self) -> None:
        """Timer callback — start the nightly memory consolidation when due.
        The run itself is a background thread; it aborts between batches the
        moment real input arrives (the robot's work always wins)."""
        self._consolidator.maybe_run(should_abort=self._has_pending_work)

    def _poll_briefing(self) -> None:
        """Timer callback — inject the scheduled morning briefing turn.
        mark_done() BEFORE enqueueing: a crash between the two loses one
        briefing, which beats delivering it twice."""
        if not self._briefing.due():
            return
        self._briefing.mark_done()
        self._enqueue_system(
            "[SYSTEM] Morning briefing time — deliver the household briefing now.")

    def _has_pending_work(self) -> bool:
        with self._queue_lock:
            return (self._turn_active or self._user_pending is not None
                    or bool(self._system_pending) or bool(self._telegram_pending))

    # ── Worker loop ───────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        self._ready_event.wait()  # wait for startup check to complete

        while True:
            self._input_event.wait()

            # Drain ONE item per iteration — priority: system events, then the
            # voice user (someone is standing there), then Telegram. Nothing is
            # discarded: whatever stays pending keeps the event armed for the
            # next loop.
            with self._queue_lock:
                telegram = None
                if self._system_pending:
                    text, is_system = self._system_pending.pop(0), True
                elif self._user_pending is not None:
                    text, is_system = self._user_pending, False
                    self._user_pending = None
                elif self._telegram_pending:
                    telegram = self._telegram_pending.pop(0)
                    text, is_system = telegram.text, False
                else:
                    text, is_system = None, False
                if not (self._system_pending or self._user_pending is not None
                        or self._telegram_pending):
                    self._input_event.clear()

            if text:
                if not is_system and telegram is None:
                    text = self._await_continuation(text)
                self._process(text, is_system, telegram=telegram)

    def _await_continuation(self, text: str) -> str:
        """Hold a voice utterance that ends mid-thought and merge what follows.

        Only voice: a Telegram message arrives whole, and a [SYSTEM] turn is
        machine-written. Merging in _on_user_input alone could not work — the
        worker empties the pending slot within milliseconds — so the wait lives
        here, and only for a transcript that reads unfinished (dangling
        conjunction, preposition or filler). Finished speech pays nothing.
        """
        if self._merge_window <= 0:
            return text
        deadline = time.monotonic() + self._merge_window
        while looks_incomplete(text):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._input_event.wait(remaining):
                break
            with self._queue_lock:
                more, self._user_pending = self._user_pending, None
                if not (self._system_pending or self._telegram_pending):
                    self._input_event.clear()
                # A continuation is not a barge-in.
                self._turn_interrupt.clear()
            if not more:
                break
            text = join_utterances(text, more)
            self.get_logger().info(f"Merged continued utterance: {text!r}")
            deadline = time.monotonic() + self._merge_window
        return text

    # ── Telegram turn framing (worker thread) ─────────────────────────────

    def _frame_telegram_turn(self, telegram, text: str) -> str:
        """Compose the turn text for an inbound Telegram message: sender tag,
        photo note, and — when this sender owes someone an answer — the open
        errand, so the agent closes the loop even hours after history trimmed.

        Errands asked out loud can't be answered in this turn (its reply sink
        is the sender's chat, and there is no speak() tool), so they become a
        [SYSTEM] turn right behind it — the existing proactive-speech path."""
        tag = f"Telegram from {telegram.name}"
        if telegram.photo:
            tag += " — photo attached"
        notes = []
        for e in get_errand_store().pop_for_sender(telegram.name):
            if e.asked_via == "voice":
                self._enqueue_system(
                    f'[SYSTEM] {telegram.name} replied to the message you relayed '
                    f'for the user ("{e.gist}"). Their reply: "{text[:300]}" — '
                    f'announce it to the user now.')
            else:
                notes.append(
                    f' [This may answer the errand {e.asked_by} gave you '
                    f'("{e.gist}") — forward the reply to {e.asked_by} with '
                    f'send_telegram_message, then acknowledge {telegram.name}.]')
        return f"[{tag}]{''.join(notes)} {text}".rstrip()

    # ── LangSmith tracing helpers ─────────────────────────────────────────
    # Tracing is opt-in (LANGROBO_TRACING=true + a key; services.config scrubs
    # the env otherwise), so everything here is a no-op on a robot that has not
    # opted in. See PI5_VOICE.md / OPERATIONS.md for what the UI then shows.

    def _trace_metadata(self, trace: str, source: str, entry_agent: str,
                        telegram=None) -> dict:
        """Metadata attached to a traced turn — searchable in the LangSmith UI.

        Lets you pull up "every telegram turn from Mom", "every turn that
        entered at navigate", or "every turn answered while the Mac Mini was
        down" without grepping logs. `trace_id` is the join key back to the
        JSON logs and the /diag/timing waterfall for the same turn.
        """
        llm = llm_module.status()
        md = {
            "trace_id": trace,
            "channel": source,                 # voice | telegram | system
            "entry_agent": entry_agent,
            "sticky_agent": self._sticky_agent,
            "llm_provider": llm.get("provider"),
            "llm_base_url": llm.get("base_url"),
            # False → the primary (Mac Mini) is in its failure cooldown, so this
            # turn is answered by the CLOUD fallback; the child chat-model run's
            # model name confirms which one actually served it.
            "llm_primary_available": llm.get("primary_available"),
            "llm_fallback": llm.get("fallback"),
        }
        if telegram is not None:
            md.update({
                "sender_name": telegram.name,
                "sender_role": telegram.role,
                "telegram_photo": telegram.photo is not None,
            })
        elif source == "voice" and self._stt_meta is not None:
            at, stt = self._stt_meta
            if time.time() - at <= self.STT_META_MAX_AGE_S:
                md.update({f"stt_{k}": v for k, v in stt.items()})
        return md

    @contextmanager
    def _trace_span(self, name: str, inputs: dict, metadata: dict, tags=None):
        """A standalone LangSmith run for work that never enters the graph.

        The fast path answers movement commands with zero LLM calls, so it
        produces no LangChain runs at all — without this it would be an
        invisible gap in the trace list ("why did 'stop' never show up?").
        Yields the run (call .end(outputs=...)) or None when tracing is off.
        """
        try:
            from langsmith.utils import tracing_is_enabled
            from langsmith import trace as ls_trace
            enabled = tracing_is_enabled()
        except Exception:       # langsmith absent or incompatible — never fatal
            enabled = False
        if not enabled:
            yield None
            return
        try:
            span = ls_trace(name=name, run_type="chain", inputs=inputs,
                            metadata=metadata, tags=tags or [])
            run = span.__enter__()
        except Exception:
            self.get_logger().debug(f"LangSmith span {name!r} failed to start")
            yield None
            return
        # Driven by hand rather than `with`: a `with` inside a @contextmanager
        # can't catch the caller's exception and then yield again (the generator
        # must stop after a throw). This forwards the caller's exception to the
        # span so it is marked as errored, re-raises it untouched, and swallows
        # only failures of the tracing teardown itself.
        exc_info = (None, None, None)
        try:
            yield run
        except BaseException:
            import sys
            exc_info = sys.exc_info()
            raise
        finally:
            try:
                span.__exit__(*exc_info)
            except Exception:
                self.get_logger().debug(f"LangSmith span {name!r} failed to close")

    # ── Graph invocation (worker thread) ──────────────────────────────────

    def _process(self, text: str, is_system: bool = False, telegram=None) -> None:
        """One turn. `telegram` (a TelegramInbound) switches the reply sink:
        voice turns stream to TTS; telegram turns answer the sender's chat and
        never touch the speaker. Both share the same history and graph."""
        from langchain_core.messages import HumanMessage
        trace = new_trace()   # stamps every log line + timing event this turn
        # One word for where this turn came from — used for the LangSmith run
        # name/tags and metadata so the trace list is filterable by channel.
        source = "system" if is_system else ("telegram" if telegram else "voice")
        self._trace_for_tts = trace   # read by _on_tts_meta on the spin thread
        turn_start = time.time()
        metrics.inc("turns_total")
        if is_system:
            metrics.inc("system_turns_total")
        if telegram:
            metrics.inc("telegram_turns_total")
        # Under _queue_lock: _on_user_input reads _turn_active/_turn_channel
        # under the same lock to decide barge-in — unlocked writes here could
        # let a new utterance miss the interrupt on a just-started turn.
        with self._queue_lock:
            self._turn_interrupt.clear()
            self._turn_channel = "telegram" if telegram else "voice"
            self._turn_active = True
        if not telegram:
            # /brain/thinking drives the robot's physical "thinking" cue —
            # meaningless (and misleading) for a phone conversation.
            self._pub_thinking.publish(Bool(data=True))
        try:
            # ── Deterministic movement fast-path (voice + text Telegram) ───
            # Exact movement commands skip the graph entirely: no supervisor,
            # no LLM, no KV-cache traffic — the intent regex either matches
            # with certainty or falls through to the normal LLM route. The
            # exchange is appended to history as a plain text turn (append-only
            # → cache-safe) so the LLM keeps full context of what the robot did.
            # Telegram turns reply to the sender's chat (never the speaker) and
            # route the deferred nav-arrival report back to that chat; photo
            # turns always take the graph (the image needs the VLM).
            if self._fast_path and not is_system and not (telegram and telegram.photo):
                from langrobo_core import fastpath
                from langchain_core.messages import AIMessage
                with self._trace_span(
                        f"fastpath:{source}",
                        inputs={"text": text},
                        metadata=self._trace_metadata(trace, source, "fastpath", telegram),
                        tags=[f"channel:{source}", "entry:fastpath"]) as span:
                    if telegram:
                        spoken = fastpath.try_handle(
                            text,
                            say_fn=lambda m: self._telegram.send_message(telegram.chat_id, m),
                            state={"channel": "telegram", "sender_name": telegram.name,
                                   "messages": []})
                    else:
                        spoken = fastpath.try_handle(text)
                    if span is not None:
                        # handled=false means the regex declined and the turn
                        # falls through to the graph — the LLM trace follows.
                        span.end(outputs={"handled": spoken is not None,
                                          "spoken": spoken})
                if spoken is not None:
                    timing.emit("fastpath_done", trace=trace)
                    self.get_logger().info(f"Fast-path handled: {text!r} → {spoken[:120]}")
                    metrics.inc("fastpath_turns_total")
                    with self._history_lock:
                        self._history.append(HumanMessage(
                            content=self._frame_telegram_turn(telegram, text)
                            if telegram else text))
                        self._history.append(AIMessage(content=spoken))
                    self._memory.record_turn(text, spoken, agent="fastpath")
                    self._start_cache_warm()
                    return

            with self._history_lock:
                history = list(self._history)

            # Plain-text user turn. Camera frames enter the conversation only when
            # local_agent calls look() — no ambient frame-stapling. Per-agent
            # projection (langrobo_core.utils.message_utils) strips images for
            # text agents.
            # Telegram turns are framed with the sender so the model knows who
            # is talking and from where — plain text, append-only, cache-safe.
            turn_text = text
            turn_msg = None
            if telegram:
                turn_text = self._frame_telegram_turn(telegram, text)
                if telegram.photo:
                    # Same shape look() uses: images ride in user-role messages
                    # (llama.cpp honours them only there) and persist in the
                    # shared history for follow-ups. Text agents get the
                    # image-stripped projection and hand over to local_agent.
                    import base64
                    b64 = base64.b64encode(telegram.photo).decode()
                    turn_msg = HumanMessage(content=[
                        {"type": "text", "text": turn_text},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ])
                self._telegram.send_typing(telegram.chat_id)
            messages = history + [turn_msg or HumanMessage(content=turn_text)]

            # Sticky routing: re-enter the previous agent for a user follow-up;
            # [SYSTEM] events always get a fresh supervisor route. turn_entry
            # enforces which agents are actually sticky-eligible and defaults
            # everything else to chat (the single-call common path).
            incoming_agent = "supervisor" if is_system else (self._sticky_agent or "chat")

            self.get_logger().info(
                f"Invoking graph with input: {text} (entry={incoming_agent}, "
                f"channel={self._turn_channel}, trace={trace})")
            timing.emit("graph_start", entry=incoming_agent, system=is_system, trace=trace)

            # Fresh handler per turn: streams sentence chunks to TTS while the
            # LLM generates. Pre-tool text ("Let me check.") is spoken as the
            # tool runs; the turn's utterance is closed with the EOU marker below.
            # Telegram turns don't stream — the reply goes out whole as one
            # phone message, and nothing may reach the speaker.
            speech_stream = (
                SpeechStreamHandler(self._bridge.publish_speech_chunk)
                if self._stream_speech and not telegram else None
            )
            callbacks = [self._timing_handler] + ([speech_stream] if speech_stream else [])

            # LangSmith run config. Naming + tagging every turn is what makes
            # the trace list explorable: the UI groups by run_name, filters on
            # tags, and searches metadata. `trace_id` is the SAME id that
            # stamps this turn's JSON log lines and /diag/timing events, so a
            # LangSmith run, `journalctl -u langrobo-brain`, and the latency
            # waterfall can all be joined on it. Costs nothing when tracing is
            # off (LANGROBO_TRACING unset → services.config scrubs the env).
            run_config = {
                "callbacks": callbacks,
                "run_name": f"turn:{source}",
                "tags": [f"channel:{source}", f"entry:{incoming_agent}"],
                "metadata": self._trace_metadata(
                    trace, source, incoming_agent, telegram),
            }

            result = None
            interrupted = False
            for event in self._graph.stream(
                {"messages": messages, "active_agent": incoming_agent,
                 # "system" marks proactive turns (quiet-hours gate in the
                 # telegram tools); _turn_channel stays voice/telegram — it
                 # governs barge-in and the reply sink, and a [SYSTEM] turn
                 # speaks aloud like any voice turn.
                 "channel": "system" if is_system else self._turn_channel,
                 "sender_name": telegram.name if telegram else None,
                 "sender_role": telegram.role if telegram else None},
                config=run_config,
                stream_mode="values"):
                if self._turn_interrupt.is_set():
                    # Barge-in: a newer utterance is waiting. Abandon this turn
                    # at the step boundary — close any partial speech so the
                    # Jetson releases the mic, keep history/sticky untouched,
                    # and let the worker loop pick up the new input.
                    interrupted = True
                    break
                if "messages" in event:
                    msg = event["messages"][-1]
                    self.get_logger().info(f"Step message [{type(msg).__name__}]: {str(msg.content)[:200]} (tool_calls: {getattr(msg, 'tool_calls', None)})")
                result = event

            timing.emit("graph_end", interrupted=interrupted)

            if interrupted:
                if speech_stream and speech_stream.chunks_sent:
                    self._bridge.publish_speech_end()
                self.get_logger().info("Turn abandoned — newer user input (barge-in)")
                metrics.inc("turns_interrupted_total")
                return

            response = self._extract_response(result)

            if not response:
                if speech_stream and speech_stream.chunks_sent:
                    # Something was streamed but the graph ended without a final
                    # text — close the utterance so the Jetson releases the mic.
                    self._bridge.publish_speech_end()
                if telegram:
                    # A texter gets an answer or an apology — never silence.
                    self._telegram.send_message(
                        telegram.chat_id, "Sorry, I couldn't come up with a reply.")
                self.get_logger().warning("Graph returned empty response")
                metrics.inc("empty_responses_total")
                # Return WITHOUT touching history or sticky: a turn that isn't
                # persisted must not change routing state either (same invariant
                # as barge-in above) — otherwise the next turn enters an agent
                # whose context was discarded with this turn.
                return

            # Remember where the turn ended so the next user follow-up can skip
            # the supervisor (turn_entry gates which agents are sticky-eligible).
            sticky = result.get("active_agent") if result else None
            # A specialist ending its turn with handover("supervisor") leaves
            # active_agent="supervisor" — persisting THAT as sticky made the
            # next user turn enter at the supervisor: a routing hop whose
            # prompt evicts the specialist slot and re-prefills the whole
            # history (~20-50s measured on the 12B, 2026-07-06). Fresh turns
            # belong at chat (the one-LLM-call common path; it carries the
            # full routing table); only [SYSTEM] events force the supervisor,
            # and agent_node does that explicitly via is_system.
            self._sticky_agent = None if sticky == "supervisor" else sticky

            # Persist the FULL message list from the graph (including any frames
            # captured via look()), so follow-up turns reason over the same image.
            # Trim at a turn boundary so the cached image prefix stays intact until
            # a deliberate reset (never a per-turn front shift).
            new_history = result.get("messages", messages) if result else messages
            with self._history_lock:
                self._history, history_reset = trim_history(new_history, self._max_history)

            if telegram:
                # Reply sink: the sender's chat, never the speaker.
                self.get_logger().info(f"→ Telegram ({telegram.name}): {response[:120]}")
                err = self._telegram.send_message(telegram.chat_id, response)
                if err:
                    self.get_logger().warning(f"Telegram reply not delivered — {err}")
            elif speech_stream and speech_stream.spoke(response):
                self.get_logger().info(f"→ TTS: {response[:120]}")
                # Final text already went out sentence-by-sentence — just close
                # the utterance. (Any pre-tool acks streamed earlier are part of
                # the same utterance.)
                timing.emit("speech_stream_done", chunks=speech_stream.chunks_sent)
                self._bridge.publish_speech_end()
            else:
                self.get_logger().info(f"→ TTS: {response[:120]}")
                # Streaming off, or the response never streamed (safe_invoke
                # fallback, non-streaming provider) — speak it whole. This also
                # closes any partial stream with the trailing EOU marker.
                self._bridge.publish_speech(response)

            # Long-term episodic memory: user turns only ([SYSTEM] events are
            # plumbing). Non-blocking — embedding happens on the memory
            # service's writer thread, never on the turn path. Telegram turns
            # carry verified identity → the reserved `person` field.
            if not is_system:
                self._memory.record_turn(text, response,
                                         agent=self._sticky_agent or "chat",
                                         person=telegram.name if telegram else None)

            # Warm the next turn's prompt after EVERY turn, not just trims.
            # A specialist excursion appends messages chat's slot has never
            # seen; without this the NEXT user turn paid that delta prefill
            # up front (~40s measured after a knowledge turn, 2026-07-06).
            # When the prefix is already cached the warm is a near-free
            # no-op, and it skips itself if input is pending.
            # (history_reset — a trim — is the expensive case it originally
            # covered; the same call handles both.)
            self._start_cache_warm()

        except Exception as e:
            self.get_logger().error(f"Graph error: {e}")
            metrics.inc("turn_errors_total")
            apology = "I'm having trouble right now. Please try again in a moment."
            if telegram:
                self._telegram.send_message(telegram.chat_id, apology)
            else:
                self._bridge.publish_speech(apology)
        finally:
            with self._queue_lock:
                self._turn_active = False
            self._last_turn_ts = time.time()
            metrics.set_gauge("last_turn_duration_seconds",
                              round(time.time() - turn_start, 3))
            if not telegram:
                self._pub_thinking.publish(Bool(data=False))

    # ── KV-cache warming (background) ─────────────────────────────────────

    def _probe_total_slots(self, base_url: str) -> int | None:
        """How many parallel slots the llama.cpp server actually has
        (GET /props → total_slots), so the slot map can fold gracefully when
        the server runs fewer than the configured pins (an out-of-range
        id_slot fails every request for that agent). Returns None when the
        server is unreachable — the configured map is kept in that case."""
        import json as _json
        import urllib.request
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3].rstrip("/")
        try:
            with urllib.request.urlopen(f"{root}/props", timeout=3) as r:
                total = int(_json.load(r).get("total_slots") or 0)
            self.get_logger().info(f"LLM server reports {total} parallel slots")
            return total or None
        except Exception as e:
            self.get_logger().info(
                f"Slot probe failed ({e}) — keeping the configured slot map")
            return None

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
        # Supervisor's slot is independent of the chat/local_agent warm above
        # (own slot, own prompt) — runs on every [SYSTEM] turn, so it gets its
        # own small thread instead of waiting behind (or competing with) it.
        if not (self._sup_warm_thread and self._sup_warm_thread.is_alive()):
            self._sup_warm_thread = threading.Thread(
                target=self._warm_cache, args=("supervisor",), daemon=True)
            self._sup_warm_thread.start()

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
            # Same prompt assembly the real turn will use — the warmer must
            # prefill a byte-identical prompt or it warms nothing. Registry-built
            # agents all expose it (langrobo_core.agents.BUILD_LLM_CALLS).
            from langrobo_core.agents import BUILD_LLM_CALLS
            build_llm_call = BUILD_LLM_CALLS.get(agent) or BUILD_LLM_CALLS["chat"]
            # The trailing "(warmup)" user message only diverges at the tail —
            # everything before it (the expensive part) is cached for real turns.
            llm, msgs = build_llm_call(history + [HumanMessage(content="(warmup)")])
            # Named + tagged so these don't sit in the LangSmith trace list as
            # anonymous root ChatOpenAI runs next to real turns: there are two
            # per turn (chat + supervisor), they carry the full prompt, and they
            # answer nothing. Hide them in the UI with -has(tags, "cache_warm").
            llm.bind(max_tokens=1).invoke(msgs, config={
                "run_name": f"cache_warm:{agent}",
                "tags": ["cache_warm", f"entry:{agent}"],
                "metadata": {"agent": agent, "history_messages": len(history)},
            })
            self.get_logger().info(
                f"KV-cache warmed for '{agent}' ({len(history)} history messages)")
        except Exception as e:
            self.get_logger().warning(f"KV-cache warm failed (non-fatal): {e}")

    def _extract_response(self, result: dict) -> str | None:
        """Return the last non-empty AI text from THIS turn of the graph output.

        Stops at the first HumanMessage: walking past it would pick up a
        previous turn's reply and re-speak it whenever the current turn ended
        without AI text (empty content, loop-guard edge cases)."""
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, HumanMessage):
                return None
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
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # The world model batches its writes (detections arrive at several Hz
        # and this boots off an SD card), so the last window of sightings is
        # only in RAM. Flush it — without the finally, Ctrl-C skipped
        # destroy_node entirely and took that window with it.
        try:
            world_model_service.get().save()
        except Exception:
            node.get_logger().warning("could not flush the world model on shutdown")
        node.destroy_node()
        rclpy.shutdown()
