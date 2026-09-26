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

from langrobo_core.utils.speech_stream import SPEECH_ABANDON, SPEECH_EOU, clean_for_speech


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

        # Named map locations: {name: (x, y, yaw_deg)} — populated from nav_params.
        # This is the BASE set; _rebuild_known_locations() layers fresh saved
        # locations on top of it and reassigns _known_locations wholesale, so
        # keep the config-declared set around under its own name.
        self._config_locations: dict = dict(known_locations or {})
        self._known_locations: dict = dict(self._config_locations)

        # The odom origin THIS process currently believes is live, from
        # /fusion/status's origin_epoch (None until the first message arrives).
        # Every saved location is stamped with the epoch it was measured
        # under; one stamped with a DIFFERENT epoch was measured from a
        # physical spot this session has no relationship to (see NAV_FRAME
        # above) and must not be served, or navigate_to_pose would silently
        # drive to the wrong place. See _on_fusion_status / _rebuild_known_locations.
        self._origin_epoch = None
        self._warned_stale_locations = False

        # Locations saved at runtime (save_location tool) persist across
        # restarts and shadow config defaults on name collision -- but ONLY
        # once their stamped epoch matches the live one; see above.
        self._locations_file = os.path.expanduser("~/.langrobo/locations.json")
        self._saved_locations: dict = {}   # {name: (x, y, yaw_deg, origin_epoch)}
        try:
            with open(self._locations_file) as f:
                raw = json.load(f)
            for k, v in raw.items():
                # 4 fields = post-epoch format. 3 = a file from before this
                # existed; treat as unstamped so it never gets reloaded silently
                # (it may well be from a now-gone odom origin).
                self._saved_locations[k] = tuple(v) if len(v) == 4 else (*v, None)
        except FileNotFoundError:
            pass
        except Exception as e:
            node.get_logger().warning(f"Could not load saved locations: {e}")
        # NOT merged into _known_locations yet: that happens in
        # _rebuild_known_locations, gated on origin_epoch actually being
        # known, so a stale entry is never served even in the few seconds
        # before the first /fusion/status message arrives.

        node.create_subscription(String, "/fusion/status", self._on_fusion_status, 10)

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
        # The CAMERA's own stamp for that frame, (sec, nanosec) on the Jetson's
        # clock, or None. It was dropped here until 2026-09-26, and without it
        # the Jetson could only ground a VLM pixel against its NEWEST depth
        # and pose -- 10-40 s after the photo, wherever the rover had got to
        # (rover repo INTELLIGENCE_PLAN.md B2). It is only ever echoed back to
        # the Jetson, never compared with this clock, so the Pi 5's offset
        # from the Jetson (LOCALIZATION_GAPS.md G9) does not matter.
        self._frame_camera_stamp: tuple | None = None

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
        # Abandon what is queued/playing. Same topic stt_node's stop word uses,
        # tagged so agent_node does not mistake its own message for a human
        # saying "stop" and sweep the wheels (see _on_tts_stop).
        self._tts_stop_pub      = node.create_publisher(String, "/voice/tts_stop", 10)
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
        # "Keep the depth and camera pose of THIS photo": sent the moment a
        # frame is taken for the VLM, so the later pixel_query can be grounded
        # at the photo's instant (pixel_to_goal.py AT THE MOMENT OF THE PHOTO).
        self._pixel_snapshot_pub = node.create_publisher(PointStamped, "/vision/pixel_snapshot", 10)

        # ── Exact motion on the Jetson (rover repo phase3/) ──────────────────
        # goal_exec: turns and short straight moves closed on the fused pose
        # (~1.4 cm, heading within 2 deg), outline-checked against LiDAR and
        # depth. reach: nav2 for the route, goal_exec to finish, and on failure
        # look / pass / wait, retrying. Both report on a status topic whose
        # lines carry the goal's header.stamp ("goal_stamp"), which is how a
        # result is matched to the goal that asked for it.
        self._goal_exec_goal_pub = node.create_publisher(PoseStamped, "/goal_exec/goal", 10)
        self._goal_exec_turn_pub = node.create_publisher(PoseStamped, "/goal_exec/turn", 10)
        from std_msgs.msg import Empty
        self._Empty = Empty
        self._goal_exec_cancel_pub = node.create_publisher(Empty, "/goal_exec/cancel", 10)
        self._reach_goal_pub = node.create_publisher(PoseStamped, "/reach/goal", 10)
        self._reach_cancel_pub = node.create_publisher(Empty, "/reach/cancel", 10)
        self._status_lock = threading.Lock()
        self._goal_exec_status: dict[str, list] = {}   # goal_stamp -> status dicts
        self._reach_status: dict[str, list] = {}
        node.create_subscription(String, "/goal_exec/status",
                                 lambda m: self._on_status(m, self._goal_exec_status), 10)
        node.create_subscription(String, "/reach/status",
                                 lambda m: self._on_status(m, self._reach_status), 10)

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

    def on_image(self, frame_bytes: bytes, camera_stamp: tuple | None = None) -> None:
        with self._frame_lock:
            self._latest_frame = frame_bytes
            self._frame_stamp = time.monotonic()
            self._frame_camera_stamp = camera_stamp

    def _on_compressed_image(self, msg) -> None:
        """CompressedImage -> the byte cache. msg.data is already JPEG.

        Kept separate from on_image() because that one takes raw bytes and is
        part of the bridge protocol StubBridge also implements; this is the ROS
        message adapter and only makes sense with rclpy present.
        """
        try:
            st = msg.header.stamp
            self.on_image(bytes(msg.data),
                          (st.sec, st.nanosec) if (st.sec or st.nanosec) else None)
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

    def get_frame_stamped(self, max_age_s: float | None = None) -> tuple:
        """(jpeg, camera_stamp) for the latest frame, read together so the two
        always describe the same photo; (None, None) under get_frame's rules.
        camera_stamp is (sec, nanosec) on the Jetson's clock, or None if the
        publisher left it empty."""
        with self._frame_lock:
            if self._latest_frame is None:
                return None, None
            if max_age_s is not None and time.monotonic() - self._frame_stamp > max_age_s:
                return None, None
            return self._latest_frame, self._frame_camera_stamp

    def hold_frame(self, camera_stamp: tuple) -> None:
        """Ask the Jetson to keep the depth frame and camera pose of the photo
        with this camera stamp, for a pixel_query that will come after the VLM
        has looked (10-40 s: longer than the Jetson's depth ring and TF buffer
        reach back). Fire and forget: a failed hold shows up in that query's
        reason ("snapshot_expired"). No-op for a frame with no stamp."""
        if not camera_stamp:
            return
        msg = self._PointStamped()
        msg.header.stamp.sec, msg.header.stamp.nanosec = int(camera_stamp[0]), int(camera_stamp[1])
        self._pixel_snapshot_pub.publish(msg)

    def get_known_locations(self) -> dict:
        return self._known_locations

    def _rebuild_known_locations(self) -> None:
        """Recompute _known_locations from config + only the saved locations
        whose stamped epoch matches the currently-live odom origin. Called
        whenever origin_epoch changes -- including mid-session, e.g. a
        cuVSLAM divergence recovery (./rover pose) restarts the odom origin
        without the Pi 5 process ever restarting."""
        fresh = {k: v[:3] for k, v in self._saved_locations.items()
                 if v[3] == self._origin_epoch}
        stale = sorted(set(self._saved_locations) - set(fresh))
        if stale and not self._warned_stale_locations:
            self._warned_stale_locations = True
            self._node.get_logger().warning(
                f"{len(stale)} saved location(s) are from a different odom "
                f"origin and will not be served until re-saved: {stale}")
        self._known_locations = {**self._config_locations, **fresh}

    def _on_fusion_status(self, msg) -> None:
        try:
            data = json.loads(msg.data)
        except (json.JSONDecodeError, TypeError):
            return
        epoch = data.get("origin_epoch")
        if epoch is None or epoch == self._origin_epoch:
            return
        self._origin_epoch = epoch
        self._warned_stale_locations = False   # a new origin deserves its own warning
        self._rebuild_known_locations()

    def get_origin_epoch(self):
        """The odom origin this session's coordinates are measured from
        (/fusion/status origin_epoch), or None before the first message.
        Anything remembered in odom -- saved locations, object memory -- is
        only meaningful under the same epoch."""
        return self._origin_epoch

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
        """Add/overwrite a named location for THIS odom session, and persist it
        to disk stamped with the current origin_epoch. It will only be served
        again after a restart (or a mid-session pose reset) if that same
        origin is still the live one -- see _rebuild_known_locations. Usable
        immediately either way, for the rest of the current session."""
        self._known_locations[name] = (x, y, yaw_deg)
        self._saved_locations[name] = (x, y, yaw_deg, self._origin_epoch)
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

    def ground_pixel(self, u: float, v: float, timeout: float = 4.0,
                     stamp: tuple | None = None, box: tuple | None = None) -> dict:
        """Ask the Jetson to turn a COLOR-image pixel into a NAV_FRAME Nav2
        goal (deproject depth → NAV_FRAME → pull back by the approach standoff).
        The Jetson's pixel_to_goal node publishes odom, matching NAV_FRAME.
        Returns the pixel_to_goal JSON result, or ok=False on timeout —
        which means the query never arrived (node down / link), NOT that
        grounding failed; grounding failures come back with a reason.

        stamp: the photo's camera stamp (get_frame_stamped). With it the
        Jetson grounds against the depth and camera pose of THAT photo
        (see hold_frame); without it, against its newest ones -- right only
        if nothing has moved since the photo.

        box: the VLM's (x0, y0, x1, y1) around the object. The Jetson then
        takes the nearest solid slab above the floor inside it instead of the
        depth at one pixel (which, on a thin object, is often the background)."""
        import uuid
        req_id = uuid.uuid4().hex[:8]
        msg = self._PointStamped()
        # The box rides in the id string ("<id>;box=..."): the reply's id is <id>.
        msg.header.frame_id = req_id + (
            ";box=" + ",".join(f"{a:.0f}" for a in box) if box else "")
        if stamp:
            msg.header.stamp.sec, msg.header.stamp.nanosec = int(stamp[0]), int(stamp[1])
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
        """Publish one sentence chunk immediately (SpeechStreamHandler sink).

        Cleaned here rather than at each caller: this is the ONE point every
        spoken path passes through — streamed chunks, whole replies, the
        startup line, the error apology — so markup cannot reach the speaker
        by some other route.
        """
        text = clean_for_speech(text)
        if not text:
            return                      # was only markup; nothing to say
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

    def publish_speech_stop(self) -> None:
        """Abandon the current utterance — drop what is queued AND playing.

        publish_speech_end() only closes the protocol: tts_node keeps speaking
        the sentences it has already synthesised, so an abandoned answer went
        on talking underneath the next one. This drops them.
        """
        self.publish_timing({"stage": "speech_abandon", "t": time.time()})
        self._tts_stop_pub.publish(String(data=SPEECH_ABANDON))

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

    # ── Exact moves (Jetson goal_exec) ─────────────────────────────────────
    #
    # These replace the TIMED twists movement.py used to drive for every turn
    # and short move: 5.0 rad/s for a computed duration, open loop. Graded on
    # 2026-09-24 (INTEGRATION_GAPS.md §6) that slid 31-68 cm per 90 deg turn
    # and once stalled at 67 of 90 deg while reporting "Movement done".
    # goal_exec closes the move on fusion2's pose, checks the swept outline
    # against the LiDAR and depth first and throughout, and says why when it
    # refuses. Both calls BLOCK until the move ends (like the timed drive did)
    # and return:
    #   {"ok": bool, "result": "reached" | "refused" | "stalled" | "failed" |
    #    "cancelled" | "interrupted" | "timeout" | "no_answer" | "no_pose" |
    #    "unavailable", "why": str, "turned_deg": float, "moved_cm": float}
    # "unavailable" = goal_exec is not running; the caller may fall back.

    def _on_status(self, msg, store: dict) -> None:
        try:
            d = json.loads(msg.data)
        except (json.JSONDecodeError, TypeError):
            return
        key = d.get("goal_stamp")
        if not key:
            return
        with self._status_lock:
            if key not in store and len(store) > 16:
                store.pop(next(iter(store)))
            store.setdefault(key, []).append(d)

    def _stamp_now(self) -> tuple:
        """(header stamp, key): a unique id for a goal, echoed back in status."""
        st = self._node.get_clock().now().to_msg()
        return st, f"{st.sec}.{st.nanosec:09d}"

    def _pose_msg(self, x: float, y: float, yaw_rad: float, stamp) -> PoseStamped:
        p = PoseStamped()
        p.header.frame_id = self.NAV_FRAME
        p.header.stamp = stamp
        p.pose.position.x, p.pose.position.y = float(x), float(y)
        p.pose.orientation.z = math.sin(yaw_rad / 2.0)
        p.pose.orientation.w = math.cos(yaw_rad / 2.0)
        return p

    def turn_by(self, degrees: float, timeout: float = 40.0) -> dict:
        """Turn in place by `degrees` (+ left), closed on the measured heading.
        The shorter way round: callers split turns over ~170 deg."""
        pose = self.get_current_pose()
        if pose is None:
            return {"ok": False, "result": "no_pose", "why": "no odom -> base_link fix"}
        x, y, yaw = pose
        return self._run_goal_exec(self._goal_exec_turn_pub, x, y,
                                   math.radians(yaw + degrees), timeout, pose)

    def drive_by(self, metres: float, timeout: float = 60.0) -> dict:
        """Drive straight `metres` (+ forward, - back), ending on the line to
        ~1.5 cm, heading held."""
        pose = self.get_current_pose()
        if pose is None:
            return {"ok": False, "result": "no_pose", "why": "no odom -> base_link fix"}
        x, y, yaw = pose
        th = math.radians(yaw)
        return self._run_goal_exec(self._goal_exec_goal_pub, x + metres * math.cos(th),
                                   y + metres * math.sin(th), th, timeout, pose)

    def _run_goal_exec(self, pub, x: float, y: float, yaw_rad: float,
                       timeout: float, start: tuple) -> dict:
        if pub.get_subscription_count() == 0:
            return {"ok": False, "result": "unavailable",
                    "why": "goal_exec is not running on the Jetson (./rover nav)"}
        stamp, key = self._stamp_now()
        pub.publish(self._pose_msg(x, y, yaw_rad, stamp))
        t0 = time.monotonic()
        out = None
        while out is None:
            if self.motion_interrupted():
                self._goal_exec_cancel_pub.publish(self._Empty())
                out = {"ok": False, "result": "interrupted", "why": "stopped by a new command"}
                break
            with self._status_lock:
                lines = list(self._goal_exec_status.get(key, []))
            done = [d for d in lines if d.get("state") == "done"]
            if done:
                d = done[-1]
                out = {"ok": d.get("result") == "reached",
                       "result": d.get("result") or "failed", "why": d.get("why", "")}
            elif not lines and time.monotonic() - t0 > 3.0:
                # It reports the instant a goal arrives. Silence is not
                # "unavailable" -- it may still act on it -- so no fallback.
                self._goal_exec_cancel_pub.publish(self._Empty())
                out = {"ok": False, "result": "no_answer", "why": "goal_exec did not acknowledge the goal"}
            elif time.monotonic() - t0 > timeout:
                self._goal_exec_cancel_pub.publish(self._Empty())
                out = {"ok": False, "result": "timeout", "why": f"no result in {timeout:.0f} s"}
            else:
                time.sleep(0.05)
        with self._status_lock:
            self._goal_exec_status.pop(key, None)
        end = self.get_current_pose()
        if end is not None:
            out["turned_deg"] = round((end[2] - start[2] + 180.0) % 360.0 - 180.0, 1)
            out["moved_cm"] = round(math.hypot(end[0] - start[0], end[1] - start[1]) * 100.0, 1)
        return out

    # How navigate_to_pose / approach goals are driven. "reach" (default): the
    # Jetson's reach_node -- nav2 for the route, goal_exec for an exact finish,
    # and on failure it clears, looks, passes a tight gap or waits, and tries
    # again (plain nav2 gave up on a 51 cm corridor in 15 s, 2026-09-26).
    # "nav2": the plain NavigateToPose action, as before. reach falls back to
    # nav2 by itself when reach_node is not running.
    NAV_BACKEND = os.environ.get("LANGROBO_NAV_BACKEND", "reach").strip().lower()

    def start_nav_to_pose(self, x: float, y: float, yaw_deg: float, label: str = "") -> None:
        """Start driving to (x, y, yaw) in NAV_FRAME asynchronously -- through
        reach_node, or plain Nav2 (see NAV_BACKEND).

        Returns immediately. When navigation completes or fails, the registered
        nav_done_callback is invoked from a background thread with (success, message).
        """
        worker, args = self._nav_worker, (x, y, yaw_deg, label)
        if self.NAV_BACKEND == "reach":
            if self._reach_goal_pub.get_subscription_count() > 0:
                # The new goal's key is current BEFORE the old goal is
                # cancelled, so the old worker sees it has been superseded and
                # does not send /reach/cancel, which could land after this
                # goal and kill it. reach preempts on a new goal by itself.
                stamp, key = self._stamp_now()
                self._reach_current_key = key
                worker, args = self._reach_worker, (x, y, yaw_deg, label, stamp, key)
            else:
                self._node.get_logger().warning(
                    "nav: reach_node is not running on the Jetson -- plain nav2 "
                    "(no exact finish, no retries). ./rover nav starts it.")
        self.cancel_navigation()

        cancel_event = threading.Event()
        with self._nav_lock:
            self._nav_cancel_event = cancel_event

        thread = threading.Thread(
            target=worker,
            args=(*args, cancel_event),
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

            from action_msgs.msg import GoalStatus as _GS
            _TERMINAL = {_GS.STATUS_SUCCEEDED, _GS.STATUS_ABORTED,
                         _GS.STATUS_CANCELED}
            _RESULT_GRACE_S = 5.0     # let the result arrive normally first
            _HARD_LIMIT_S = 900.0     # nothing may wait here forever

            # STATUS BACKSTOP. This loop used to have exactly two exits, the
            # result future and an explicit cancel, and on 2026-09-10 it found
            # the third: nav2 aborted the goal (bt_navigator logged "Goal
            # failed" after three backup recoveries) and the result future
            # never resolved. The thread span here forever, _fire_nav_done was
            # never called, and the user was told "I'll say when I'm there"
            # by a robot that had already given up. Nothing logged, because
            # the only code that logs is the code that never ran.
            #
            # So trust the action's STATUS TOPIC as well as its result. rclpy
            # keeps goal_handle.status fresh from /navigate_to_pose/_action/
            # status independently of the result service, so a terminal status
            # is proof the goal is over even when the result never lands.
            # Grace period first, because the result carries more detail.
            waited = 0.0
            terminal_since = None
            while not result_event.wait(timeout=1.0):
                waited += 1.0
                if cancel_event.is_set():
                    goal_handle.cancel_goal_async()
                    dest = f"'{label}'" if label else f"({x:.1f}, {y:.1f})"
                    self._fire_nav_done(False, f"Navigation to {dest} cancelled")
                    return

                try:
                    status = goal_handle.status
                except Exception:
                    status = None

                if status in _TERMINAL:
                    if terminal_since is None:
                        terminal_since = waited
                        self._node.get_logger().warning(
                            f"nav: goal reached terminal status {status} but no "
                            f"result yet — waiting {_RESULT_GRACE_S:.0f}s for it")
                    elif waited - terminal_since >= _RESULT_GRACE_S:
                        dest = f"'{label}'" if label else f"({x:.1f}, {y:.1f})"
                        self._node.get_logger().error(
                            f"nav: result never arrived for terminal status "
                            f"{status}; reporting from status instead")
                        if status == _GS.STATUS_SUCCEEDED:
                            self._fire_nav_done(True, f"I've arrived at {dest}.")
                        elif status == _GS.STATUS_CANCELED:
                            self._fire_nav_done(
                                False, f"Navigation to {dest} cancelled")
                        else:
                            self._fire_nav_done(
                                False,
                                f"Navigation to {dest} failed — path may be "
                                f"blocked.")
                        return
                else:
                    terminal_since = None

                if waited >= _HARD_LIMIT_S:
                    dest = f"'{label}'" if label else f"({x:.1f}, {y:.1f})"
                    goal_handle.cancel_goal_async()
                    self._node.get_logger().error(
                        f"nav: no result and no terminal status after "
                        f"{_HARD_LIMIT_S:.0f}s — giving up")
                    self._fire_nav_done(
                        False,
                        f"Navigation to {dest} never finished — I've stopped "
                        f"waiting and cancelled the goal.")
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

    _reach_current_key: str | None = None

    def _reach_worker(self, x: float, y: float, yaw_deg: float, label: str,
                      stamp, key: str, cancel_event: threading.Event) -> None:
        """Background thread: one goal through reach_node, until its final line."""
        dest = f"'{label}'" if label else f"({x:.1f}, {y:.1f})"
        try:
            self._reach_goal_pub.publish(self._pose_msg(x, y, math.radians(yaw_deg), stamp))
            t0 = time.monotonic()
            while True:
                if cancel_event.wait(timeout=0.5):
                    if self._reach_current_key == key:     # a stop, not a newer goal
                        self._reach_cancel_pub.publish(self._Empty())
                    self._fire_nav_done(False, f"Navigation to {dest} cancelled")
                    return
                with self._status_lock:
                    lines = list(self._reach_status.get(key, []))
                final = [d for d in lines if "result" in d]
                if final:
                    d = final[-1]
                    if d["result"] == "reached":
                        self._fire_nav_done(True, f"I've arrived at {dest}.")
                    elif d["result"] == "cancelled":
                        self._fire_nav_done(False, f"Navigation to {dest} cancelled")
                    else:
                        tried = d.get("tried") or []
                        why = tried[-1] if tried else d.get("why", "")
                        self._fire_nav_done(
                            False, f"Navigation to {dest} failed after retrying -- {why}."
                            if why else f"Navigation to {dest} failed.")
                    return
                waited = time.monotonic() - t0
                if not lines and waited > 10.0:
                    self._fire_nav_done(
                        False, f"Navigation to {dest} did not start -- the Jetson's "
                        f"reach node did not answer.")
                    return
                if waited > 900.0:              # reach gives up by itself at 300 s
                    self._reach_cancel_pub.publish(self._Empty())
                    self._fire_nav_done(
                        False, f"Navigation to {dest} never finished -- I've stopped "
                        f"waiting and cancelled the goal.")
                    return
        except Exception as e:
            self._fire_nav_done(False, f"Navigation error: {e}")
        finally:
            with self._status_lock:
                self._reach_status.pop(key, None)

    def _fire_nav_done(self, success: bool, message: str) -> None:
        # Logged unconditionally, and says whether a listener existed. A silent
        # arrival is indistinguishable from a nav that never finished unless
        # this line is in the log (2026-09-10: a goal failed after 44 s of
        # follow_path aborts and nothing anywhere recorded that it had).
        cb = self._nav_done_callback
        self._node.get_logger().info(
            f"nav done: success={success} listener={'yes' if cb else 'NONE'} "
            f"msg={message!r}")
        if cb is None:
            # Not a curiosity: this is a navigation that finished and told
            # nobody. It happened for two sessions on 2026-09-10 because
            # LangGraph Studio was running beside the brain, polling the same
            # Telegram bot, and its /studio_bridge ran the goal -- only
            # agent_node registers this callback. The rover drove, failed, and
            # the user waited for a message that had already been discarded.
            self._node.get_logger().error(
                "nav done with NO listener — this completion is being thrown "
                "away and nobody will be told. If LangGraph Studio is running "
                "next to the brain, stop it: `pkill -f 'langgrap[h] dev'`, "
                "then check `ros2 node list | grep studio` is empty.")
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
