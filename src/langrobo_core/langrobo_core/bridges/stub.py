"""StubBridge — no-op replacement for langrobo_ros.ros2_bridge.ROS2Bridge.

Used by graph_studio.py (LangGraph Studio) and the tests so the whole graph
runs on any machine without ROS2. Every public method from ROS2Bridge is
implemented here as a no-op that logs the call.

Keep this class in step with ROS2Bridge's public surface. A method missing
here is not a test-only problem: Studio and every off-robot run hit it as an
AttributeError raised inside a tool, which the model then reports to the user
as a robot fault. `ground_pixel` was missing exactly that way, which meant
approach_described_object — the rover's only working object-approach path —
could not be exercised off-robot at all.

Tools that import ROS2 message types inside their body (movement.py) raise
ImportError when ROS2 packages are absent — LangGraph catches that as a
ToolMessage error and the LLM responds gracefully.
"""

import logging
import os

logger = logging.getLogger(__name__)


class StubBridge:

    def __init__(self, known_locations: dict = None):
        self._known_locations = known_locations or {}
        self._nav_done_callback = None
        self._system_turn_callback = None
        self.system_turns: list[str] = []   # tests inspect what was enqueued
        logger.info("StubBridge initialised (no ROS2 — all publishes are logged)")

    # ── Self-initiated turns ──────────────────────────────────────────────

    def register_system_turn_callback(self, cb) -> None:
        self._system_turn_callback = cb

    def enqueue_system_turn(self, text: str) -> None:
        self.system_turns.append(text)
        logger.info("[STUB] enqueue_system_turn: %s", text)
        if self._system_turn_callback:
            self._system_turn_callback(text)

    # ── Camera ────────────────────────────────────────────────────────────

    def on_image(self, frame_bytes: bytes) -> None:
        pass

    def get_frame(self, max_age_s: float | None = None) -> bytes | None:
        """No live camera in Studio. For testing local_agent's look() + vision,
        set STUDIO_TEST_IMAGE to a JPEG/PNG path and that frame is served
        instead."""
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

    # ── Pose and locations ────────────────────────────────────────────────

    def get_current_pose(self):
        # Settable so tests can reach the no-localisation branch: the real
        # bridge returns None when TF has no odom->base_link fix, and tools
        # are required to stay honest about that rather than invent a position.
        return getattr(self, "pose", (0.0, 0.0, 0.0))

    def add_known_location(self, name, x, y, yaw_deg):
        self._known_locations[name] = (x, y, yaw_deg)

    def get_known_locations(self) -> dict:
        return self._known_locations

    # ── VLM pixel grounding (Jetson pixel_to_goal) ────────────────────────

    def ground_pixel(self, u: float, v: float, timeout: float = 4.0) -> dict:
        """No Jetson in Studio, so there is no depth to ground a pixel against.

        Returns the same shape the real bridge returns when the Jetson is
        silent, so the calling tool takes its honest "the depth service isn't
        answering" branch instead of raising."""
        logger.info("[STUB] ground_pixel(%.0f, %.0f) -> no_reply_from_jetson", u, v)
        return {"ok": False, "reason": "no_reply_from_jetson"}

    # ── Camera pan/tilt ───────────────────────────────────────────────────

    def set_pan_tilt(self, pan_deg: float, tilt_deg: float) -> None:
        self._pan_tilt = (pan_deg, tilt_deg)
        logger.info("[STUB] set_pan_tilt(%.0f, %.0f)", pan_deg, tilt_deg)

    def get_pan_tilt(self) -> tuple:
        return getattr(self, "_pan_tilt", (0.0, 0.0))

    # ── Speech ────────────────────────────────────────────────────────────

    def publish_speech(self, text: str) -> None:
        logger.info("[STUB] speak: %s", text)

    def publish_speech_chunk(self, text: str) -> None:
        logger.info("[STUB] speak chunk: %s", text)

    def publish_speech_end(self) -> None:
        logger.info("[STUB] end of utterance")

    def publish_timing(self, event: dict) -> None:
        pass

    # ── Publishers ────────────────────────────────────────────────────────

    @property
    def robot_body(self) -> str:
        return "stub"

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
