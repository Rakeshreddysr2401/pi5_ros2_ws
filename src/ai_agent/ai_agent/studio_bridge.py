"""StubBridge — no-op replacement for ROS2Bridge when ROS2 is unavailable.

Used by graph_studio.py so LangGraph Studio can run on a machine without ROS2
(or before the robot is booted).  Every public method from ROS2Bridge is
implemented here as a no-op that logs the call.

Tools that import ROS2 message types inside their body (movement.py, system.py)
will raise ImportError when ROS2 packages are absent — LangGraph catches that
as a ToolMessage error and the LLM can respond gracefully.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)


class StubBridge:

    def __init__(self, known_locations: dict = None):
        self._known_locations = known_locations or {}
        self._active_order_id: str | None = None
        self._order_lock = threading.Lock()
        self._nav_done_callback = None
        logger.info("StubBridge initialised (no ROS2 — all publishes are logged)")

    # ── Topics ────────────────────────────────────────────────────────────

    def on_image(self, frame_bytes: bytes) -> None:
        pass

    def on_target_result(self, msg) -> None:
        pass

    def get_frame(self) -> bytes | None:
        """No live camera in Studio. For testing local_agent's look() + vision,
        set STUDIO_TEST_IMAGE to a JPEG/PNG path and that frame is served instead.
        """
        path = os.getenv("STUDIO_TEST_IMAGE", "").strip()
        if not path:
            return None
        try:
            with open(os.path.expanduser(path), "rb") as f:
                return f.read()
        except OSError as e:
            logger.warning("STUDIO_TEST_IMAGE unreadable (%s): %s", path, e)
            return None

    def get_known_locations(self) -> dict:
        return self._known_locations

    def set_vision_target(self, target: str) -> None:
        logger.info("[STUB] set_vision_target: %s", target)

    def get_target_result(self) -> dict | None:
        # No Jetson target_node in Studio — report "not found" so the approach
        # loop scans briefly and exits instead of hanging.
        logger.info("[STUB] get_target_result -> None")
        return None

    # ── Active order ──────────────────────────────────────────────────────

    def set_active_order(self, order_id: str | None) -> None:
        with self._order_lock:
            self._active_order_id = order_id
        logger.info("[STUB] set_active_order(%s)", order_id)

    def get_active_order(self) -> str | None:
        with self._order_lock:
            return self._active_order_id

    # ── Speech ────────────────────────────────────────────────────────────

    def publish_speech(self, text: str) -> None:
        logger.info("[STUB] speak: %s", text)

    def publish_speech_chunk(self, text: str) -> None:
        logger.info("[STUB] speak chunk: %s", text)

    def publish_speech_end(self) -> None:
        logger.info("[STUB] end of utterance")

    # ── Publishers ────────────────────────────────────────────────────────

    def publish_twist(self, twist) -> None:
        lx = getattr(getattr(twist, "linear",  None), "x", 0.0)
        az = getattr(getattr(twist, "angular", None), "z", 0.0)
        logger.info("[STUB] publish_twist: linear.x=%.2f angular.z=%.2f", lx, az)

    def publish_to_topic(self, topic: str, data: str) -> None:
        logger.info("[STUB] publish_to_topic(%s): %s", topic, data)

    # ── Services ──────────────────────────────────────────────────────────

    def call_service(self, name: str, srv_type, request_msg, timeout: float = 5.0):
        raise TimeoutError(f"[STUB] Service '{name}' unavailable (ROS2 not running)")

    # ── Actions (Nav2) ────────────────────────────────────────────────────

    def register_nav_done_callback(self, cb) -> None:
        self._nav_done_callback = cb

    def cancel_navigation(self) -> None:
        logger.info("[STUB] cancel_navigation()")

    def request_motion_stop(self) -> None:
        logger.info("[STUB] request_motion_stop()")

    def clear_motion_stop(self) -> None:
        logger.info("[STUB] clear_motion_stop()")

    def motion_interrupted(self) -> bool:
        return False

    def start_nav_to_pose(self, x: float, y: float, yaw_deg: float, label: str = "") -> None:
        dest = f"'{label}'" if label else f"({x:.1f}, {y:.1f})"
        logger.info("[STUB] navigate_to_pose → %s", dest)
        if self._nav_done_callback:
            self._nav_done_callback(True, f"[STUB] Simulated arrival at {dest}")

    def wait_for_nav_server(self, timeout: float = 30.0) -> bool:
        return True
