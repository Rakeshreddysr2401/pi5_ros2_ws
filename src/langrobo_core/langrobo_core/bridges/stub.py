"""StubBridge — no-op replacement for langrobo_ros.ros2_bridge.ROS2Bridge.

Used by graph_studio.py (LangGraph Studio) and the smoke tests so the whole
graph runs on any machine without ROS2.  Every public method from ROS2Bridge
is implemented here as a no-op that logs the call.

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

    def get_frame(self, max_age_s: float | None = None) -> bytes | None:
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

    def frame_age(self) -> float | None:
        # The STUDIO_TEST_IMAGE frame (if any) is always "fresh".
        return 0.0 if self.get_frame() is not None else None

    def get_known_locations(self) -> dict:
        return self._known_locations

    def set_vision_target(self, target: str) -> None:
        logger.info("[STUB] set_vision_target: %s", target)

    def get_target_result(self, max_age_s: float | None = None) -> dict | None:
        # No Jetson target_node in Studio — report "no detection" so the
        # approach loop holds still and exits instead of hanging.
        logger.info("[STUB] get_target_result -> None")
        return None

    # ── Music ─────────────────────────────────────────────────────────────

    def music_command(self, cmd: dict) -> None:
        logger.info("[STUB] music_command: %s", cmd)
        # Simulate the Jetson music_node confirming playback so play_music's
        # confirmation wait doesn't block Studio turns for 10s. cmd_t echoes
        # the command's `t` — the token play_music matches on.
        import time
        if cmd.get("action") == "play":
            self._music_state = {"playing": True, "paused": False,
                                 "title": f"[stub] {cmd.get('query', '')}",
                                 "volume": 70, "stamp": time.time(),
                                 "cmd_t": cmd.get("t")}
        elif cmd.get("action") == "stop":
            self._music_state = {"playing": False, "paused": False,
                                 "title": "", "volume": 70, "stamp": time.time()}
        elif cmd.get("action") in ("pause", "resume"):
            if getattr(self, "_music_state", None):
                self._music_state["paused"] = cmd["action"] == "pause"
                self._music_state["stamp"] = time.time()
        self._music_state_seq = getattr(self, "_music_state_seq", 0) + 1

    def get_music_state(self, max_playing_age_s: float | None = None) -> dict | None:
        return getattr(self, "_music_state", None)

    def get_music_state_seq(self) -> int:
        return getattr(self, "_music_state_seq", 0)

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
