"""Agent node — thin ROS2 entry point.

Responsibilities (and nothing more):
  1. Declare / read ROS2 parameters
  2. Create ROS2Bridge and wire all subscriptions
  3. Inject bridge + LLM config into the graph layer
  4. Build the LangGraph graph
  5. Run a worker thread that drains the input queue and invokes the graph
  6. Manage conversation history and multimodal user messages
"""

import base64
import os
import queue
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
        self.declare_parameter("base_url",   "http://192.168.31.24:8080/v1")
        self.declare_parameter("max_tokens", 3000)
        self.declare_parameter("history_turns", 20)
        self.declare_parameter("use_vision",  True)
        self.declare_parameter("vision_query_timeout", 60.0)

        provider      = self.get_parameter("provider").value
        model         = self.get_parameter("model").value
        api_key_env   = self.get_parameter("api_key_env").value
        base_url      = self.get_parameter("base_url").value
        max_tokens    = self.get_parameter("max_tokens").value
        self._max_history    = self.get_parameter("history_turns").value * 2
        self._use_vision     = self.get_parameter("use_vision").value
        self._vision_timeout = self.get_parameter("vision_query_timeout").value

        api_key = os.environ.get(api_key_env, "") if api_key_env else "none"

        # ── Inject config into graph layer ────────────────────────────────
        self._bridge = ROS2Bridge(self)
        bridge_module.init(self._bridge)
        llm_module.configure(provider, model, base_url, api_key, max_tokens)

        # ── Build graph ───────────────────────────────────────────────────
        self._graph   = build_graph()
        self._history: list[dict] = []
        self._history_lock = threading.Lock()
        self._input_queue: queue.Queue = queue.Queue(maxsize=3)

        # ── Publishers ────────────────────────────────────────────────────
        self._pub_thinking = self.create_publisher(Bool, "/brain/thinking", 1)

        # ── Subscribers ───────────────────────────────────────────────────
        self.create_subscription(String, "/voice/user_input", self._on_user_input, 10)

        if self._use_vision:
            from cv_bridge import CvBridge
            from sensor_msgs.msg import Image
            self._cv_bridge = CvBridge()
            self.create_subscription(Image,  "/vision/image_raw",    self._on_image,                    1)
            self.create_subscription(String, "/vision/objects_3d",   self._bridge.on_objects,           1)
            self.create_subscription(String, "/vision/query_result", self._bridge.on_query_result,     10)
            self.get_logger().info("Vision enabled — image_raw + objects_3d + query_result")

        # ── Worker thread ─────────────────────────────────────────────────
        threading.Thread(target=self._worker_loop, daemon=True).start()

        self.get_logger().info(
            f"Agent ready — provider: {provider}, base_url: {base_url}, "
            f"vision: {self._use_vision}"
        )

    # ── Image callback (spin thread) ──────────────────────────────────────

    def _on_image(self, msg) -> None:
        import cv2
        try:
            cv_img = self._cv_bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            _, buf = cv2.imencode(".jpg", cv_img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            self._bridge.on_image(buf.tobytes())
        except Exception as e:
            self.get_logger().warning(f"Frame encode error: {e}")

    # ── Input queue (spin thread → worker thread) ─────────────────────────

    def _on_user_input(self, msg: String) -> None:
        text = msg.data.strip()
        if not text:
            return
        try:
            self._input_queue.put_nowait(text)
        except queue.Full:
            try:
                self._input_queue.get_nowait()
            except queue.Empty:
                pass
            self._input_queue.put_nowait(text)
            self.get_logger().warning("Input queue full — oldest item dropped")

    def _worker_loop(self) -> None:
        while True:
            text = self._input_queue.get()
            self._process(text)
            self._input_queue.task_done()

    # ── Graph invocation (worker thread) ──────────────────────────────────

    def _process(self, text: str) -> None:
        self._pub_thinking.publish(Bool(data=True))
        try:
            with self._history_lock:
                history = list(self._history)

            user_content = self._build_user_content(text)
            messages     = history + [{"role": "user", "content": user_content}]

            result   = self._graph.invoke({"messages": messages})
            response = self._extract_response(result)

            if not response:
                self.get_logger().warning("Graph returned empty response")
                return

            with self._history_lock:
                self._history.append({"role": "user",      "content": text})
                self._history.append({"role": "assistant", "content": response})
                if len(self._history) > self._max_history:
                    self._history = self._history[-self._max_history:]

            self.get_logger().info(f"→ TTS: {response[:120]}")
            self._bridge.publish_speech(response)

        except Exception as e:
            self.get_logger().error(f"Graph error: {e}")
        finally:
            self._pub_thinking.publish(Bool(data=False))

    def _build_user_content(self, text: str):
        """Plain text, or [text + image] when a frame is cached and vision is on."""
        if not self._use_vision:
            return text
        frame = self._bridge.get_frame()
        if frame is None:
            return text
        b64 = base64.b64encode(frame).decode()
        return [
            {"type": "text",      "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]

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
