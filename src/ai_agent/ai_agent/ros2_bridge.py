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
from std_msgs.msg import String


class ROS2Bridge:

    def __init__(self, node, known_locations: dict = None):
        self._node = node

        # Named map locations: {name: (x, y, yaw_deg)} — populated from nav_params
        self._known_locations: dict = known_locations or {}

        # Locks
        self._frame_lock   = threading.Lock()
        self._pub_lock     = threading.Lock()
        self._svc_lock     = threading.Lock()
        self._act_lock     = threading.Lock()

        # ── Vision query blocking sync ─────────────────────────────────────
        self._vision_lock   = threading.Lock()
        self._vision_event  = threading.Event()
        self._vision_result: str | None = None

        # ── Active Swiggy order (for background delivery polling) ─────────────
        self._order_lock       = threading.Lock()
        self._active_order_id: str | None = None

        # ── Lazy client registries ─────────────────────────────────────────
        self._dynamic_pubs:    dict = {}   # topic name → Publisher
        self._service_clients: dict = {}   # service name → Client
        self._action_clients:  dict = {}   # action name → ActionClient

        # ── Fixed publishers (pre-created so tools never block on first call)
        self._speech_pub       = node.create_publisher(String, "/voice/robot_speech", 10)
        self._vision_query_pub = node.create_publisher(String, "/vision/query", 10)
        self._twist_pub        = node.create_publisher(Twist, "/cmd_vel", 10)
        self._goal_pub         = node.create_publisher(PoseStamped, "/goal_pose", 10)

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
        return "Vision query timed out — moondream node may not be running"

    # ── Active order ──────────────────────────────────────────────────────

    def set_active_order(self, order_id: str | None) -> None:
        with self._order_lock:
            self._active_order_id = order_id

    def get_active_order(self) -> str | None:
        with self._order_lock:
            return self._active_order_id

    # ── Publishers ─────────────────────────────────────────────────────────

    def publish_speech(self, text: str) -> None:
        self._speech_pub.publish(String(data=text))

    def publish_twist(self, twist: Twist) -> None:
        """Publish Twist directly to /cmd_vel → micro-ROS agent → ESP32."""
        self._twist_pub.publish(twist)

    def publish_goal_pose(self, x: float, y: float, yaw_deg: float) -> None:
        """Publish PoseStamped to /goal_pose → Jetson Nav2 for map-based navigation."""
        msg = PoseStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.pose.position.x = x
        msg.pose.position.y = y
        yaw_rad = math.radians(yaw_deg)
        msg.pose.orientation.z = math.sin(yaw_rad / 2.0)
        msg.pose.orientation.w = math.cos(yaw_rad / 2.0)
        self._goal_pub.publish(msg)

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
    # SECTION 3 — Actions
    # ══════════════════════════════════════════════════════════════════════════

    def send_action(
        self,
        name:        str,
        action_type,
        goal_msg,
        timeout:     float = 60.0,
        feedback_cb: Optional[Callable] = None,
    ):
        """Send a ROS2 action goal and block until complete or timeout.

        Action clients are created lazily on first call and cached.
        Optional feedback_cb(feedback_msg) is called on each feedback message.
        Raises TimeoutError or RuntimeError on failure.

        Args:
            name:        Action server name, e.g. '/navigate_to_pose'
            action_type: ROS2 action class, e.g. nav2_msgs.action.NavigateToPose
            goal_msg:    Populated goal object
            timeout:     Max seconds to wait for the action to complete
            feedback_cb: Optional callable(feedback) for progress updates

        Example:
            from nav2_msgs.action import NavigateToPose
            from geometry_msgs.msg import PoseStamped
            goal = NavigateToPose.Goal()
            goal.pose = PoseStamped()
            goal.pose.pose.position.x = 1.0
            result = bridge.send_action('/navigate_to_pose', NavigateToPose, goal, timeout=120.0)
        """
        from rclpy.action import ActionClient

        with self._act_lock:
            if name not in self._action_clients:
                self._action_clients[name] = ActionClient(self._node, action_type, name)

        client = self._action_clients[name]
        if not client.wait_for_server(timeout_sec=min(10.0, timeout)):
            raise TimeoutError(f"Action server '{name}' not available")

        # ── Send goal ─────────────────────────────────────────────────────
        goal_event  = threading.Event()
        goal_box:   list = [None]

        def _goal_response(future):
            goal_box[0] = future.result()
            goal_event.set()

        send_future = client.send_goal_async(goal_msg, feedback_callback=feedback_cb)
        send_future.add_done_callback(_goal_response)

        if not goal_event.wait(timeout=10.0):
            raise TimeoutError(f"Action '{name}': goal acceptance timed out")

        goal_handle = goal_box[0]
        if not goal_handle.accepted:
            raise RuntimeError(f"Action '{name}': goal was rejected by the server")

        # ── Wait for result ────────────────────────────────────────────────
        result_event = threading.Event()
        result_box:  list = [None]

        def _result(future):
            result_box[0] = future.result()
            result_event.set()

        goal_handle.get_result_async().add_done_callback(_result)

        if not result_event.wait(timeout=timeout):
            raise TimeoutError(f"Action '{name}' timed out after {timeout}s")
        return result_box[0]
