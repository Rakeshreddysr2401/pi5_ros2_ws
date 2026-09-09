"""ROS2Bridge — ROS2 I/O for the langrobo brain (imports rclpy/ROS2 types).

Three sections mirror the three ROS2 communication patterns:
  1. Topics   — fast pub/sub with sensor caching
  2. Services — synchronous request / response
  3. Actions  — long-running goals with optional feedback callbacks

LangGraph tools never import from this file directly — they call
langrobo_core.tools._bridge.get() which returns this object.
"""

import json
import os
import threading
import time
from typing import Callable

import math

from geometry_msgs.msg import PoseStamped, Twist
from std_msgs.msg import Bool, String

from langrobo_core.utils.speech_stream import SPEECH_EOU


class ROS2Bridge:

    # The ONE frame this brain navigates and localises in.
    #
    # Every goal sent to Nav2, every pose read from TF, and every 3D detection
    # accepted from the Jetson must agree on this. They did not: "map" was
    # hardcoded in three separate places, written against a fuller perception
    # stack that this rover does not have. This rover's Nav2 runs single-session
    # in `odom` with no relocalisation (nav2.yaml: global_frame: odom, in the
    # rover repo), so nothing ever publishes a map frame — get_current_pose()
    # returned None on every call and every navigate_to_pose/approach_* goal
    # was silently untransformable. Two of the three were fixed by hand on
    # 2026-09-06; a third — the detections handler — was missed entirely and
    # silently dropped every detection it was ever given.
    #
    # It is a single name now precisely so they can never drift apart again.
    # Set LANGROBO_NAV_FRAME=map on a rig that really does run AMCL or cuVSLAM
    # map-relocalisation.
    #
    # Anything publishing object positions for this brain to navigate to must
    # express them in THIS frame — see INTEGRATION_GAPS.md §1 for the
    # /vision/detections_3d contract.
    NAV_FRAME = os.environ.get("LANGROBO_NAV_FRAME", "odom")

    def __init__(self, node, known_locations: dict = None, robot_body: str = "rover",
                 use_vision: bool = True):
        self._node = node
        # Which body this brain drives cmd_vel to: the real ESP32 rover (plain
        # Twist on /cmd_vel) or the Gazebo sim rover_sim (TwistStamped on
        # /mecanum_drive_controller/cmd_vel) — see CLAUDE.md "Simulation
        # laptop". Switched by scripts/fleet.sh {sim|rover}, default "rover"
        # so the real robot's behaviour never changes unless sim is requested.
        self._robot_body = robot_body

        # Named map locations: {name: (x, y, yaw_deg)} — populated from nav_params
        self._known_locations: dict = known_locations or {}

        # Locations saved at runtime (save_location tool) persist across
        # restarts and shadow yaml defaults on name collision.
        self._locations_file = os.path.expanduser("~/.langrobo/locations.json")
        self._saved_locations: dict = {}
        try:
            with open(self._locations_file) as f:
                self._saved_locations = {k: tuple(v) for k, v in json.load(f).items()}
            self._known_locations.update(self._saved_locations)
        except FileNotFoundError:
            pass
        except Exception as e:
            node.get_logger().warning(f"Could not load saved locations: {e}")

        # TF buffer for get_current_pose() (map -> base_link)
        from tf2_ros import Buffer, TransformListener
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, node)

        # ── Locks ─────────────────────────────────────────────────────────────
        self._frame_lock   = threading.Lock()
        self._pub_lock     = threading.Lock()
        self._act_lock     = threading.Lock()

        # ── Pixel-grounding replies (JSON from Jetson pixel_to_goal) ─────────
        # Keyed by the request id we sent in the query's frame_id; ground_pixel
        # polls for its own id so concurrent queries can't steal each other's
        # answer.
        self._pixel_lock = threading.Lock()
        self._pixel_results: dict[str, dict] = {}

        # ── Latest camera frame (bytes, JPEG-encoded) ─────────────────────────
        self._latest_frame: bytes | None = None
        self._frame_stamp: float = 0.0   # time.monotonic() of last frame

        # Commanded pan/tilt (open-loop; servos settle in ~0.3s). Kept so tools
        # can re-centre and report the current aim without a state topic.
        self._pan_tilt = (0.0, 0.0)

        # ── Speech state ───────────────────────────────────────────────────────
        # No Pi5-side queue: sentence chunks are published immediately and the
        # Jetson tts_node queues/plays them in order (utterance ends with the
        # SPEECH_EOU marker). _is_speaking is informational (stop-keyword later).
        self._speech_lock    = threading.Lock()
        self._is_speaking    = False   # updated by /voice/tts_speaking subscription

        # ── Active navigation state ────────────────────────────────────────────
        self._nav_lock      = threading.Lock()
        self._nav_cancel_event: threading.Event | None = None
        self._nav_thread:       threading.Thread | None = None

        # ── Motion interrupt ───────────────────────────────────────────────────
        # Set by agent_node when new user input arrives, so blocking motion tools
        # (navigate_to_visible_object's servo loop, move_robot's timed drive) can
        # bail out promptly — i.e. a spoken "stop" actually stops the wheels.
        self._motion_interrupt = threading.Event()

        # ── Lazy client registries ─────────────────────────────────────────────
        self._dynamic_pubs:    dict = {}   # topic name → Publisher
        self._action_clients:  dict = {}   # action name → ActionClient

        # ── Registered callback for navigation completion ──────────────────────
        self._nav_done_callback: Callable[[bool, str], None] | None = None

        # ── Registered callback for tool-injected [SYSTEM] turns ───────────────
        # Lets pure-zone tools (e.g. announce_at_home) hand a proactive turn to
        # agent_node's system queue without importing anything ROS-side.
        self._system_turn_callback: Callable[[str], None] | None = None

        # ── Fixed publishers (pre-created so tools never block on first call) ──
        self._speech_pub        = node.create_publisher(String, "/voice/robot_speech", 10)
        self._sim_body = self._robot_body == "sim"
        if self._sim_body:
            from geometry_msgs.msg import TwistStamped
            self._TwistStamped = TwistStamped   # bound once — publish_twist runs at 20 Hz
            self._twist_pub = node.create_publisher(
                TwistStamped, "/mecanum_drive_controller/cmd_vel", 10)
        else:
            self._twist_pub = node.create_publisher(Twist, "/cmd_vel", 10)
        self._timing_pub        = node.create_publisher(String, "/diag/timing", 10)
        # Camera pan/tilt: raw servo angles for the ESP32 (0-180, 90=centre) +
        # a JSON state topic the Jetson TF broadcaster mirrors into the TF tree
        # (NAV_FRAME detections stay correct while the head is turned).
        from std_msgs.msg import UInt16
        self._UInt16 = UInt16
        self._servo_pan_pub  = node.create_publisher(UInt16, "/servo_pan", 10)
        self._servo_tilt_pub = node.create_publisher(UInt16, "/servo_tilt", 10)
        self._pan_tilt_state_pub = node.create_publisher(String, "/camera/pan_tilt_state", 10)

        # VLM pixel grounding (Jetson pixel_to_goal): PointStamped pixel query
        # in, JSON result out — see langrobo_perception pixel_to_goal_node.py.
        from geometry_msgs.msg import PointStamped
        self._PointStamped = PointStamped
        self._pixel_query_pub = node.create_publisher(PointStamped, "/vision/pixel_query", 10)
        node.create_subscription(String, "/vision/pixel_result", self._on_pixel_result, 10)

        # Subscribe to Kokoro speaking status (half-duplex state, stop-keyword later)
        node.create_subscription(Bool, "/voice/tts_speaking", self._on_speaking, 10)

        # ── Vision input ──────────────────────────────────────────────────
        # This belongs HERE because this class owns the cache it fills:
        # get_frame()/frame_age(). It used to be wired in agent_node instead,
        # which meant any OTHER owner of a bridge got the cache with nothing to
        # fill it -- graph_studio.py (LangGraph Studio) had a fully wired bridge
        # whose look() returned "no camera frame is available" forever, on a
        # robot whose camera was publishing fine.
        # /vision/pixel_result is already subscribed above for the same reason;
        # the image half was simply left outside. Same failure family as the
        # NAV_FRAME note at the top of this class: wiring that lives in more
        # than one place drifts, and the half nobody is looking at goes quiet.
        if use_vision:
            from sensor_msgs.msg import CompressedImage
            # Consume the JPEG camera_node already publishes -- no raw-frame
            # transport over the Jetson<->Pi5 link and no re-encode on the Pi5.
            # The compressed bytes ARE what look() needs (base64 image/jpeg).
            node.create_subscription(CompressedImage, "/camera/color/image_raw/compressed",
                                     self._on_compressed_image, 1)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — Topics
    # ══════════════════════════════════════════════════════════════════════════

    # ── Callbacks (ROS2 spin thread) ───────────────────────────────────────

    def on_image(self, frame_bytes: bytes) -> None:
        with self._frame_lock:
            self._latest_frame = frame_bytes
            self._frame_stamp = time.monotonic()

    def _on_compressed_image(self, msg) -> None:
        """CompressedImage -> the byte cache. msg.data is already JPEG.

        Kept separate from on_image() because that one takes raw bytes and is
        part of the bridge protocol StubBridge also implements; this is the ROS
        message adapter and only makes sense with rclpy present.
        """
        try:
            self.on_image(bytes(msg.data))
        except Exception as e:
            self._node.get_logger().warning(f"Frame cache error: {e}")

    def _on_speaking(self, msg: Bool) -> None:
        with self._speech_lock:
            self._is_speaking = msg.data

    # ── Cached reads (worker thread) ──────────────────────────────────────

    def get_frame(self, max_age_s: float | None = None) -> bytes | None:
        """Latest camera JPEG, or None if there is none — or it is older than
        max_age_s (camera node dead / Jetson link down: better to admit
        blindness than confidently describe a scene that is long gone)."""
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            if max_age_s is not None and time.monotonic() - self._frame_stamp > max_age_s:
                return None
            return self._latest_frame

    def frame_age(self) -> float | None:
        """Seconds since the last camera frame arrived (None = never)."""
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return time.monotonic() - self._frame_stamp

    def get_known_locations(self) -> dict:
        return self._known_locations

    def get_current_pose(self) -> tuple | None:
        """Robot pose as (x, y, yaw_deg) in NAV_FRAME; None if TF has no fix."""
        import rclpy.time
        try:
            t = self._tf_buffer.lookup_transform(
                self.NAV_FRAME, "base_link", rclpy.time.Time())
        except Exception:
            return None
        q = t.transform.rotation
        yaw = math.degrees(math.atan2(2 * (q.w * q.z + q.x * q.y),
                                      1 - 2 * (q.y * q.y + q.z * q.z)))
        return (t.transform.translation.x, t.transform.translation.y, yaw)

    def add_known_location(self, name: str, x: float, y: float, yaw_deg: float) -> None:
        """Add/overwrite a named location and persist it across restarts."""
        self._known_locations[name] = (x, y, yaw_deg)
        self._saved_locations[name] = (x, y, yaw_deg)
        os.makedirs(os.path.dirname(self._locations_file), exist_ok=True)
        with open(self._locations_file, "w") as f:
            json.dump({k: list(v) for k, v in self._saved_locations.items()}, f, indent=2)

    # ── Camera pan/tilt (ESP32 dual servo + Jetson TF mirror) ─────────────

    def set_pan_tilt(self, pan_deg: float, tilt_deg: float) -> None:
        """Aim the camera head: pan -90..90 (0 = forward), tilt -30..30
        (0 = level). Publishes raw servo angles (90 + deg) for the ESP32 and a
        JSON state for the Jetson's pan_tilt TF broadcaster."""
        self._pan_tilt = (pan_deg, tilt_deg)
        self._servo_pan_pub.publish(self._UInt16(data=int(round(90 + pan_deg))))
        self._servo_tilt_pub.publish(self._UInt16(data=int(round(90 + tilt_deg))))
        self._pan_tilt_state_pub.publish(String(data=json.dumps(
            {"pan_deg": pan_deg, "tilt_deg": tilt_deg, "t": time.time()})))

    def get_pan_tilt(self) -> tuple:
        """Last commanded (pan_deg, tilt_deg) — open-loop state."""
        return self._pan_tilt

    def _on_pixel_result(self, msg) -> None:
        """Cache a /vision/pixel_result JSON reply under its request id."""
        try:
            data = json.loads(msg.data)
        except (json.JSONDecodeError, TypeError):
            return
        req_id = data.get("id")
        if not req_id:
            return
        with self._pixel_lock:
            # Keep the map tiny — replies are consumed within seconds.
            if len(self._pixel_results) > 32:
                self._pixel_results.clear()
            self._pixel_results[req_id] = data

    def ground_pixel(self, u: float, v: float, timeout: float = 4.0) -> dict:
        """Ask the Jetson to turn a COLOR-image pixel into a NAV_FRAME Nav2
        goal (deproject depth → NAV_FRAME → pull back by the approach standoff).
        The Jetson's pixel_to_goal node publishes odom, matching NAV_FRAME.
        Returns the pixel_to_goal JSON result, or ok=False on timeout —
        which means the query never arrived (node down / link), NOT that
        grounding failed; grounding failures come back with a reason."""
        import uuid
        req_id = uuid.uuid4().hex[:8]
        msg = self._PointStamped()
        msg.header.frame_id = req_id
        msg.point.x = float(u)
        msg.point.y = float(v)
        self._pixel_query_pub.publish(msg)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._pixel_lock:
                if req_id in self._pixel_results:
                    return self._pixel_results.pop(req_id)
            time.sleep(0.05)
        return {"ok": False, "reason": "no_reply_from_jetson"}

    # ── Timing events (/diag/timing) ──────────────────────────────────────

    def publish_timing(self, event: dict) -> None:
        """Sink for graph.utils.timing — one JSON stage event per message."""
        self._timing_pub.publish(String(data=json.dumps(event)))

    # ── Speech (streamed utterance protocol) ──────────────────────────────
    # An utterance = 1..N text chunks followed by the SPEECH_EOU marker. The
    # Jetson tts_node plays chunks in order and holds /voice/tts_speaking True
    # (mic muted) until the marker arrives.

    def publish_speech_chunk(self, text: str) -> None:
        """Publish one sentence chunk immediately (SpeechStreamHandler sink)."""
        self.publish_timing({"stage": "speech_publish", "t": time.time(), "chars": len(text)})
        self._speech_pub.publish(String(data=text))

    def publish_speech_end(self) -> None:
        """Terminate the current utterance — lets tts_node release the mic."""
        self.publish_timing({"stage": "speech_eou", "t": time.time()})
        self._speech_pub.publish(String(data=SPEECH_EOU))

    def publish_speech(self, text: str) -> None:
        """Publish a complete utterance (non-streamed path: startup, fallbacks)."""
        self.publish_speech_chunk(text)
        self.publish_speech_end()

    # ── Publishers ─────────────────────────────────────────────────────────

    @property
    def robot_body(self) -> str:
        """'rover' or 'sim' — which body cmd_vel is currently wired to."""
        return self._robot_body

    def publish_twist(self, twist: Twist) -> None:
        """Publish a Twist command to the robot body's cmd_vel — rover gets a
        plain Twist on /cmd_vel (ESP32/micro-ROS); sim gets the same linear/
        angular values wrapped in TwistStamped on
        /mecanum_drive_controller/cmd_vel (rover_sim's Nav2/mecanum contract,
        see docs/INTERFACE.md in the rover_sim repo)."""
        if self._sim_body:
            stamped = self._TwistStamped()
            stamped.header.stamp = self._node.get_clock().now().to_msg()
            # mecanum_drive_controller.cpp never reads the command's incoming
            # frame_id (only sets it on its OWN published odometry) — so this
            # value doesn't affect driving. Set to "base_footprint" anyway to
            # match the sim's actual configured base_frame_id (rover_sim's
            # rover_description/config/rosmaster_x3/ros2_controllers.yaml),
            # for correctness with any tooling (rviz/rqt) that does read it.
            stamped.header.frame_id = "base_footprint"
            stamped.twist = twist
            self._twist_pub.publish(stamped)
        else:
            self._twist_pub.publish(twist)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — Actions (Nav2)
    # ══════════════════════════════════════════════════════════════════════════

    def register_nav_done_callback(self, cb: Callable[[bool, str], None]) -> None:
        """Register a callback(success: bool, message: str) called when navigation ends."""
        self._nav_done_callback = cb

    # ── Tool-injected [SYSTEM] turns ────────────────────────────────────────

    def register_system_turn_callback(self, cb: Callable[[str], None]) -> None:
        """Register agent_node's _enqueue_system so pure-zone tools can inject
        proactive turns (the standard [SYSTEM] producer pattern)."""
        self._system_turn_callback = cb

    def enqueue_system_turn(self, text: str) -> None:
        """Queue a [SYSTEM] turn (FIFO, never dropped, spoken via the normal
        proactive path). Called from tools on the worker thread — the callback
        only appends to agent_node's locked queue, so this is thread-safe."""
        cb = self._system_turn_callback
        if cb:
            cb(text)

    def cancel_navigation(self) -> None:
        """Cancel any active navigation goal."""
        with self._nav_lock:
            if self._nav_cancel_event:
                self._nav_cancel_event.set()

    # ── Motion interrupt (blocking movement tools) ─────────────────────────
    def request_motion_stop(self) -> None:
        """Signal blocking motion tools to abort (e.g. on new user input)."""
        self._motion_interrupt.set()

    def clear_motion_stop(self) -> None:
        """Clear the interrupt — call at the start of a deliberate motion tool."""
        self._motion_interrupt.clear()

    def motion_interrupted(self) -> bool:
        return self._motion_interrupt.is_set()

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
            # One frame for goals, poses and detections alike — see NAV_FRAME.
            goal.pose.header.frame_id = self.NAV_FRAME
            # stamp left zero = "use latest TF": Nav2 re-transforms the
            # ORIGINAL stamp on every replan, so a now() stamp ages out of
            # the 10s TF cache mid-drive and aborts the goal (2026-07-16).
            goal.pose.pose.position.x = x
            goal.pose.pose.position.y = y
            yaw_rad = math.radians(yaw_deg)
            goal.pose.pose.orientation.z = math.sin(yaw_rad / 2.0)
            goal.pose.pose.orientation.w = math.cos(yaw_rad / 2.0)

            goal_event = threading.Event()
            goal_box:  list = [None]

            def _goal_response(future):
                # An exception here would propagate into rclpy's executor and
                # leave goal_event unset — the caller then waits the full 10s
                # and reports a timeout for what was really a rejection.
                try:
                    goal_box[0] = future.result()
                except Exception:
                    goal_box[0] = None
                goal_event.set()

            send_future = client.send_goal_async(goal)
            send_future.add_done_callback(_goal_response)

            if not goal_event.wait(timeout=10.0):
                self._fire_nav_done(False, "Navigation goal acceptance timed out")
                return

            goal_handle = goal_box[0]
            if goal_handle is None:
                # future.result() raised inside the callback, so _goal_response
                # set the event with an empty box. Without this the next line
                # raised AttributeError and the user heard "Navigation error:
                # 'NoneType' object has no attribute 'accepted'".
                self._fire_nav_done(
                    False, "Navigation failed — Nav2 never answered the goal request")
                return
            if not goal_handle.accepted:
                self._fire_nav_done(False, "Navigation goal rejected by Nav2")
                return

            result_event = threading.Event()
            result_box:  list = [None]

            def _result(future):
                try:
                    result_box[0] = future.result()
                except Exception:
                    result_box[0] = None
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
            if result is None:
                self._fire_nav_done(
                    False, f"Navigation to {dest} ended without a result from Nav2")
                return
            from action_msgs.msg import GoalStatus
            if result.status == GoalStatus.STATUS_SUCCEEDED:
                self._fire_nav_done(True, f"I've arrived at {dest}.")
            else:
                self._fire_nav_done(False, f"Navigation to {dest} failed — path may be blocked.")

        except Exception as e:
            self._fire_nav_done(False, f"Navigation error: {e}")

    def _fire_nav_done(self, success: bool, message: str) -> None:
        # Logged unconditionally, and says whether a listener existed. A silent
        # arrival is indistinguishable from a nav that never finished unless
        # this line is in the log (2026-09-10: a goal failed after 44 s of
        # follow_path aborts and nothing anywhere recorded that it had).
        cb = self._nav_done_callback
        self._node.get_logger().info(
            f"nav done: success={success} listener={'yes' if cb else 'NONE'} "
            f"msg={message!r}")
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
