"""Agent node — thin ROS2 entry point.

Responsibilities (and nothing more):
  1. Declare / read ROS2 parameters
  2. Create ROS2Bridge and wire all subscriptions
  3. Inject bridge + LLM config into the graph layer
  4. Build the LangGraph graph
  5. Run a worker thread that drains the input queue and invokes the graph
  6. Manage conversation history and multimodal user messages
"""

import os
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from .ros2_bridge import ROS2Bridge
from .graph import llm as llm_module
from .graph.tools import _bridge as bridge_module
from .graph.graph import build_graph


class AgentNode(Node):

    def __init__(self):
        super().__init__("agent_node")

        # ── Parameters ────────────────────────────────────────────────────
        self.declare_parameter("provider",   "llamacpp")
        self.declare_parameter("model",      "default")
        self.declare_parameter("api_key_env", "")
        self.declare_parameter("base_url",   "http://singireddys-mac-mini.local:8080/v1")
        self.declare_parameter("max_tokens", 3000)
        self.declare_parameter("history_turns", 20)
        self.declare_parameter("use_vision",  True)
        self.declare_parameter("vision_query_timeout", 60.0)
        # Per-agent overrides for the local multimodal agent (Gemma via llama.cpp).
        # local_agent_slot: dedicated llama.cpp KV-cache slot (-1 = none/auto).
        # local_agent_model: override model name for local_agent ("" = inherit global).
        self.declare_parameter("local_agent_slot",  -1)
        self.declare_parameter("local_agent_model", "")

        provider      = self.get_parameter("provider").value
        model         = self.get_parameter("model").value
        api_key_env   = self.get_parameter("api_key_env").value
        base_url      = self.get_parameter("base_url").value
        max_tokens    = self.get_parameter("max_tokens").value
        # History now stores raw message objects (incl. captured image blocks),
        # not turn-pairs, so allow extra room for tool-call / image messages.
        self._max_history    = self.get_parameter("history_turns").value * 4
        self._use_vision     = self.get_parameter("use_vision").value
        self._vision_timeout = self.get_parameter("vision_query_timeout").value
        local_agent_slot     = self.get_parameter("local_agent_slot").value
        local_agent_model    = self.get_parameter("local_agent_model").value

        api_key = os.environ.get(api_key_env, "") if api_key_env else "none"

        # Per-agent LLM overrides — local_agent gets its own slot (and optionally
        # its own multimodal model) so its cached image prefix isn't evicted.
        agent_overrides = {
            "local_agent": {
                "slot":  local_agent_slot,
                "model": local_agent_model or None,
            }
        }

        # ── Known map locations for Nav2 goal publishing ──────────────────
        self.declare_parameter("locations.kitchen",     [2.5,  1.0,  0.0])
        self.declare_parameter("locations.living_room", [0.0,  3.0, 90.0])
        self.declare_parameter("locations.bedroom",     [-2.0, 2.0, 180.0])
        self.declare_parameter("locations.entrance",    [0.0,  0.0,  0.0])
        known_locations = {
            name: tuple(self.get_parameter(f"locations.{name}").value)
            for name in ("kitchen", "living_room", "bedroom", "entrance")
        }

        # ── Inject config into graph layer ────────────────────────────────
        self._bridge = ROS2Bridge(self, known_locations=known_locations)
        bridge_module.init(self._bridge)
        llm_module.configure(provider, model, base_url, api_key, max_tokens, agent_overrides)

        # Register navigation completion callback
        self._bridge.register_nav_done_callback(self._on_nav_done)

        # ── Build graph ───────────────────────────────────────────────────
        self._graph   = build_graph()
        self._history: list[dict] = []
        self._history_lock = threading.Lock()

        # ── Two-slot input queue ──────────────────────────────────────────
        # _user_pending:   latest user message (replaced by newer ones)
        # _system_pending: priority system event (delivery, nav done) — never dropped
        # _input_event:    wakes the worker when either slot is filled
        self._queue_lock     = threading.Lock()
        self._user_pending:   str | None = None
        self._system_pending: str | None = None
        self._input_event    = threading.Event()

        # ── Startup readiness gate ────────────────────────────────────────
        self._ready_event = threading.Event()

        # ── Publishers ────────────────────────────────────────────────────
        self._pub_thinking = self.create_publisher(Bool, "/brain/thinking", 1)

        # ── Subscribers ───────────────────────────────────────────────────
        self.create_subscription(String, "/voice/user_input", self._on_user_input, 10)

        if self._use_vision:
            from cv_bridge import CvBridge
            from sensor_msgs.msg import Image
            self._cv_bridge = CvBridge()
            self.create_subscription(Image,  "/camera/color/image_raw",  self._on_image,                1)
            self.create_subscription(String, "/vision/target_result",    self._bridge.on_target_result, 10)
            self.get_logger().info("Vision enabled — /camera/color/image_raw + /vision/target_result")

        # ── Worker + startup threads ──────────────────────────────────────
        threading.Thread(target=self._startup_check, daemon=True).start()
        threading.Thread(target=self._worker_loop,   daemon=True).start()

        # ── Delivery polling timer (every 2 min) ──────────────────────────
        self.create_timer(120.0, self._poll_delivery)

        self.get_logger().info(
            f"Agent starting — provider: {provider}, base_url: {base_url}, "
            f"vision: {self._use_vision}"
        )

    # ── Startup readiness check ───────────────────────────────────────────

    def _startup_check(self) -> None:
        """Announce readiness. Nav2 check skipped — not available in current hardware scope."""
        self._bridge.publish_speech("I'm ready.")
        self.get_logger().info("Startup complete")
        self._ready_event.set()

    # ── Navigation done callback (background nav thread → worker) ─────────

    def _on_nav_done(self, success: bool, message: str) -> None:
        """Called by bridge when Nav2 goal finishes. Injects system message."""
        status = "Navigation succeeded" if success else "Navigation failed"
        self._enqueue_system(f"[SYSTEM] {status}: {message}")

    # ── Image callback (spin thread) ──────────────────────────────────────

    def _on_image(self, msg) -> None:
        import cv2
        try:
            cv_img = self._cv_bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            _, buf = cv2.imencode(".jpg", cv_img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            self._bridge.on_image(buf.tobytes())
        except Exception as e:
            self.get_logger().warning(f"Frame encode error: {e}")

    # ── Two-slot queue (spin thread → worker thread) ──────────────────────

    def _on_user_input(self, msg: String) -> None:
        text = msg.data.strip()
        if not text:
            return
        # New user input cancels any active navigation and replaces pending user message
        self._bridge.cancel_navigation()
        with self._queue_lock:
            if self._user_pending is not None:
                self.get_logger().warning("User queue: replacing pending message with newer input")
            self._user_pending = text
        self._input_event.set()

    def _enqueue_system(self, text: str) -> None:
        """Enqueue a system event — never dropped, fires after current graph run."""
        with self._queue_lock:
            self._system_pending = text
        self._input_event.set()

    def _poll_delivery(self) -> None:
        """Timer callback — injects a delivery check if an order is active."""
        order_id = self._bridge.get_active_order()
        if order_id:
            self._enqueue_system(f"[SYSTEM] Check if order {order_id} has been delivered")

    # ── Worker loop ───────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        self._ready_event.wait()  # wait for startup check to complete

        while True:
            self._input_event.wait()

            # Drain both slots — system takes priority
            with self._queue_lock:
                text = self._system_pending or self._user_pending
                self._system_pending = None
                self._user_pending   = None
                self._input_event.clear()
                # If both were set, re-arm for the other slot (already cleared above)

            if text:
                self._process(text)

    # ── Graph invocation (worker thread) ──────────────────────────────────

    def _process(self, text: str) -> None:
        from langchain_core.messages import HumanMessage
        self._pub_thinking.publish(Bool(data=True))
        try:
            with self._history_lock:
                history = list(self._history)

            # Plain-text user turn. Camera frames enter the conversation only when
            # local_agent calls look() — no ambient frame-stapling. Per-agent
            # projection (graph.utils.message_utils) strips images for text agents.
            messages = history + [HumanMessage(content=text)]

            self.get_logger().info(f"Invoking graph with input: {text}")
            result = None
            for event in self._graph.stream({"messages": messages}, stream_mode="values"):
                if "messages" in event:
                    msg = event["messages"][-1]
                    self.get_logger().info(f"Step message [{type(msg).__name__}]: {str(msg.content)[:200]} (tool_calls: {getattr(msg, 'tool_calls', None)})")
                result = event

            response = self._extract_response(result)

            if not response:
                self.get_logger().warning("Graph returned empty response")
                return

            # Persist the FULL message list from the graph (including any frames
            # captured via look()), so follow-up turns reason over the same image.
            # Trim at a turn boundary so the cached image prefix stays intact until
            # a deliberate reset (never a per-turn front shift).
            new_history = result.get("messages", messages) if result else messages
            with self._history_lock:
                self._history = self._trim_history(new_history)

            self.get_logger().info(f"→ TTS: {response[:120]}")
            self._bridge.publish_speech(response)

        except Exception as e:
            self.get_logger().error(f"Graph error: {e}")
            self._bridge.publish_speech("I'm having trouble right now. Please try again in a moment.")
        finally:
            self._pub_thinking.publish(Bool(data=False))

    def _trim_history(self, messages: list) -> list:
        """Cap history length, cutting only at a HumanMessage boundary.

        Append-only within the cap (cache-friendly). When the cap is exceeded we
        drop whole leading turns — one cache reset at the boundary, never a
        per-turn front shift — and never orphan a tool_call / tool-response pair.
        """
        from langchain_core.messages import HumanMessage
        msgs = list(messages)
        if len(msgs) <= self._max_history:
            return msgs
        cut = len(msgs) - self._max_history
        while cut < len(msgs) and not isinstance(msgs[cut], HumanMessage):
            cut += 1
        return msgs[cut:] if cut < len(msgs) else msgs

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
