"""ROS2Bridge — the ONLY file in ai_agent that imports rclpy or ROS2 types.

Three sections mirror the three ROS2 communication patterns:
  1. Topics   — fast pub/sub with sensor caching
  2. Services — synchronous request / response
  3. Actions  — long-running goals with optional feedback callbacks

LangGraph tools never import from this file directly — they call
graph.tools._bridge.get() which returns this object.
"""

import threading
from typing import Callable, Optional

import math

from geometry_msgs.msg import PoseStamped, Twist
from std_msgs.msg import Bool, String


class ROS2Bridge:

    def __init__(self, node, known_locations: dict = None):
        self._node = node

        # Named map locations: {name: (x, y, yaw_deg)} — populated from nav_params
        self._known_locations: dict = known_locations or {}

        # ── Locks ─────────────────────────────────────────────────────────────
        self._frame_lock   = threading.Lock()
        self._pub_lock     = threading.Lock()
        self._svc_lock     = threading.Lock()
        self._act_lock     = threading.Lock()

        # ── Vision query blocking sync ─────────────────────────────────────────
        self._vision_lock   = threading.Lock()
        self._vision_event  = threading.Event()
        self._vision_result: str | None = None

        # ── Latest camera frame (bytes, JPEG-encoded) ─────────────────────────
        self._latest_frame: bytes | None = None

        # ── Active Swiggy order (for background delivery polling) ─────────────
        self._order_lock       = threading.Lock()
        self._active_order_id: str | None = None

        # ── Speech queue + back-pressure against Kokoro TTS ───────────────────
        self._speech_lock    = threading.Lock()
        self._speech_queue:  list[str] = []
        self._is_speaking    = False   # updated by /voice/speaking subscription

        # ── Active navigation state ────────────────────────────────────────────
        self._nav_lock      = threading.Lock()
        self._nav_cancel_event: threading.Event | None = None
        self._nav_thread:       threading.Thread | None = None

        # ── Lazy client registries ─────────────────────────────────────────────
        self._dynamic_pubs:    dict = {}   # topic name → Publisher
        self._service_clients: dict = {}   # service name → Client
        self._action_clients:  dict = {}   # action name → ActionClient

        # ── Registered callback for navigation completion ──────────────────────
        self._nav_done_callback: Callable[[bool, str], None] | None = None

        # ── Fixed publishers (pre-created so tools never block on first call) ──
        self._speech_pub       = node.create_publisher(String, "/voice/robot_speech", 10)
        self._vision_query_pub = node.create_publisher(String, "/vision/query", 10)
        self._twist_pub        = node.create_publisher(Twist, "/cmd_vel", 10)

        # Subscribe to Kokoro speaking status for back-pressure
        node.create_subscription(Bool, "/voice/speaking", self._on_speaking, 10)

        # Drain speech queue every 300ms
        node.create_timer(0.3, self._drain_speech_queue)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — Topics
    # ══════════════════════════════════════════════════════════════════════════

    # ── Callbacks (ROS2 spin thread) ───────────────────────────────────────

    def on_image(self, frame_bytes: bytes) -> None:
        with self._frame_lock:
            self._latest_frame = frame_bytes

    def on_query_result(self, msg) -> None:
        with self._vision_lock:
            self._vision_result = msg.data
            self._vision_event.set()

    def _on_speaking(self, msg: Bool) -> None:
        with self._speech_lock:
            self._is_speaking = msg.data

    # ── Cached reads (worker thread) ──────────────────────────────────────

    def get_frame(self) -> bytes | None:
        with self._frame_lock:
            return self._latest_frame

    def get_known_locations(self) -> dict:
        return self._known_locations

    # ── Blocking VLM query (topic-pair) ───────────────────────────────────

    def query_vision(self, question: str, timeout: float = 10.0) -> str:
        """Publish to /vision/query and block until /vision/query_result arrives."""
        with self._vision_lock:
            self._vision_event.clear()
            self._vision_result = None
        self._vision_query_pub.publish(String(data=question))
        if self._vision_event.wait(timeout=timeout):
            with self._vision_lock:
                return self._vision_result or "No answer received"
        return "Vision is currently unavailable — Moondream node may not be running"

    # ── Active order ──────────────────────────────────────────────────────

    def set_active_order(self, order_id: str | None) -> None:
        with self._order_lock:
            self._active_order_id = order_id

    def get_active_order(self) -> str | None:
        with self._order_lock:
            return self._active_order_id

    # ── Speech with back-pressure ─────────────────────────────────────────

    def publish_speech(self, text: str) -> None:
        """Queue speech text. Drained by timer when Kokoro is not speaking."""
        with self._speech_lock:
            self._speech_queue.append(text)

    def _drain_speech_queue(self) -> None:
        """Timer callback (spin thread): publish next queued string if not speaking."""
        with self._speech_lock:
            if self._is_speaking or not self._speech_queue:
                return
            text = self._speech_queue.pop(0)
        self._speech_pub.publish(String(data=text))

    # ── Publishers ─────────────────────────────────────────────────────────

    def publish_twist(self, twist: Twist) -> None:
        """Publish Twist directly to /cmd_vel → micro-ROS agent → ESP32."""
        self._twist_pub.publish(twist)

    def publish_to_topic(self, topic: str, data: str) -> None:
        """Publish a String to any topic, creating the publisher lazily."""
        with self._pub_lock:
            if topic not in self._dynamic_pubs:
                self._dynamic_pubs[topic] = self._node.create_publisher(String, topic, 10)
        self._dynamic_pubs[topic].publish(String(data=data))

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — Services
    # ══════════════════════════════════════════════════════════════════════════

    def call_service(self, name: str, srv_type, request_msg, timeout: float = 5.0):
        """Call a ROS2 service synchronously from the worker thread.

        Service clients are created lazily on first call and cached.
        Raises TimeoutError if the service is unavailable or slow.

        Args:
            name:        ROS2 service name, e.g. '/robot/get_status'
            srv_type:    ROS2 service class, e.g. std_srvs.srv.Trigger
            request_msg: Populated request object, e.g. Trigger.Request()
            timeout:     Max wait in seconds for both availability and response

        Example:
            from std_srvs.srv import Trigger
            resp = bridge.call_service('/robot/get_status', Trigger, Trigger.Request())
            print(resp.message)
        """
        with self._svc_lock:
            if name not in self._service_clients:
                self._service_clients[name] = self._node.create_client(srv_type, name)

        client = self._service_clients[name]
        if not client.wait_for_service(timeout_sec=timeout):
            raise TimeoutError(f"Service '{name}' not available after {timeout}s")

        # Bridge worker thread ↔ ROS2 future using a threading Event
        done_event  = threading.Event()
        result_box: list = [None]

        def _done(future):
            result_box[0] = future.result()
            done_event.set()

        client.call_async(request_msg).add_done_callback(_done)

        if not done_event.wait(timeout=timeout):
            raise TimeoutError(f"Service '{name}' call timed out after {timeout}s")
        return result_box[0]

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — Actions (Nav2)
    # ══════════════════════════════════════════════════════════════════════════

    def register_nav_done_callback(self, cb: Callable[[bool, str], None]) -> None:
        """Register a callback(success: bool, message: str) called when navigation ends."""
        self._nav_done_callback = cb

    def cancel_navigation(self) -> None:
        """Cancel any active navigation goal."""
        with self._nav_lock:
            if self._nav_cancel_event:
                self._nav_cancel_event.set()

    def start_nav_to_pose(self, x: float, y: float, yaw_deg: float, label: str = "") -> None:
        """Start a Nav2 NavigateToPose action asynchronously.

        Returns immediately. When navigation completes or fails, the registered
        nav_done_callback is invoked from a background thread with (success, message).
        """
        self.cancel_navigation()

        cancel_event = threading.Event()
        with self._nav_lock:
            self._nav_cancel_event = cancel_event

        thread = threading.Thread(
            target=self._nav_worker,
            args=(x, y, yaw_deg, label, cancel_event),
            daemon=True,
        )
        with self._nav_lock:
            self._nav_thread = thread
        thread.start()

    def _nav_worker(
        self,
        x: float,
        y: float,
        yaw_deg: float,
        label: str,
        cancel_event: threading.Event,
    ) -> None:
        """Background thread: send Nav2 action goal, wait for result."""
        try:
            from rclpy.action import ActionClient
            from nav2_msgs.action import NavigateToPose

            with self._act_lock:
                if "/navigate_to_pose" not in self._action_clients:
                    self._action_clients["/navigate_to_pose"] = ActionClient(
                        self._node, NavigateToPose, "/navigate_to_pose"
                    )
            client = self._action_clients["/navigate_to_pose"]

            if not client.wait_for_server(timeout_sec=10.0):
                self._fire_nav_done(False, "Navigation unavailable — Nav2 not running")
                return

            goal = NavigateToPose.Goal()
            goal.pose = PoseStamped()
            goal.pose.header.frame_id = "map"
            goal.pose.header.stamp = self._node.get_clock().now().to_msg()
            goal.pose.pose.position.x = x
            goal.pose.pose.position.y = y
            yaw_rad = math.radians(yaw_deg)
            goal.pose.pose.orientation.z = math.sin(yaw_rad / 2.0)
            goal.pose.pose.orientation.w = math.cos(yaw_rad / 2.0)

            goal_event = threading.Event()
            goal_box:  list = [None]

            def _goal_response(future):
                goal_box[0] = future.result()
                goal_event.set()

            send_future = client.send_goal_async(goal_msg=goal)
            send_future.add_done_callback(_goal_response)

            if not goal_event.wait(timeout=10.0):
                self._fire_nav_done(False, "Navigation goal acceptance timed out")
                return

            goal_handle = goal_box[0]
            if not goal_handle.accepted:
                self._fire_nav_done(False, "Navigation goal rejected by Nav2")
                return

            result_event = threading.Event()
            result_box:  list = [None]

            def _result(future):
                result_box[0] = future.result()
                result_event.set()

            goal_handle.get_result_async().add_done_callback(_result)

            # Poll for cancel or result
            while not result_event.wait(timeout=1.0):
                if cancel_event.is_set():
                    goal_handle.cancel_goal_async()
                    dest = f"'{label}'" if label else f"({x:.1f}, {y:.1f})"
                    self._fire_nav_done(False, f"Navigation to {dest} cancelled")
                    return

            result = result_box[0]
            dest = f"'{label}'" if label else f"({x:.1f}, {y:.1f})"
            from action_msgs.msg import GoalStatus
            if result.status == GoalStatus.STATUS_SUCCEEDED:
                self._fire_nav_done(True, f"I've arrived at {dest}.")
            else:
                self._fire_nav_done(False, f"Navigation to {dest} failed — path may be blocked.")

        except Exception as e:
            self._fire_nav_done(False, f"Navigation error: {e}")

    def _fire_nav_done(self, success: bool, message: str) -> None:
        cb = self._nav_done_callback
        if cb:
            try:
                cb(success, message)
            except Exception as e:
                self._node.get_logger().error(f"nav_done_callback raised: {e}")

    def wait_for_nav_server(self, timeout: float = 30.0) -> bool:
        """Return True if the Nav2 action server is available within timeout."""
        try:
            from rclpy.action import ActionClient
            from nav2_msgs.action import NavigateToPose

            with self._act_lock:
                if "/navigate_to_pose" not in self._action_clients:
                    self._action_clients["/navigate_to_pose"] = ActionClient(
                        self._node, NavigateToPose, "/navigate_to_pose"
                    )
            return self._action_clients["/navigate_to_pose"].wait_for_server(timeout_sec=timeout)
        except Exception:
            return False
