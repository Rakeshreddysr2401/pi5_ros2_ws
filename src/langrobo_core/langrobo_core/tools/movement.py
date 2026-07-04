import math
import time

from langchain_core.tools import tool

from . import _bridge

# Fine-movement Twist parameters (direct /cmd_vel, bypasses Nav2)
_LINEAR_VEL_MS  = 0.28   # m/s forward/backward command (translates to ~93% PWM)
_PHYSICAL_VEL_MS = 0.60   # actual physical speed of the robot at 93% PWM (calibrated from active tests)
_ANGULAR_VEL_RS = 2.8    # rad/s rotation command (translates to ~70% PWM)
_STEADY_STATE_ANGULAR_VEL = 2.65  # rad/s physical speed at 70% PWM (calibrated from active tests)
_TURN_STARTUP_DELAY       = 0.0   # seconds transient ramp-up offset
_CMD_BUFFER     = 0.2    # extra sleep after each command (seconds)


def _duration(cmd: str, val: float) -> float:
    if cmd in ("F", "B"):
        return (val / 100.0) / _PHYSICAL_VEL_MS   # cm → m
    if cmd in ("L", "R"):
        return (math.radians(val) / _STEADY_STATE_ANGULAR_VEL) + _TURN_STARTUP_DELAY
    return 0.0


def _drive_for_duration(bridge, twist, dur: float) -> bool:
    """Publish twist periodically to feed the watchdog, with millisecond-precise stop timing.

    Returns True if the move completed, False if it was interrupted (e.g. the user
    spoke a new command / "stop"). Always leaves the wheels stopped.
    """
    from geometry_msgs.msg import Twist
    start = time.time()
    end = start + dur
    last_pub = 0.0
    interrupted = False
    while True:
        now = time.time()
        if now >= end:
            break
        if bridge.motion_interrupted():
            interrupted = True
            break
        # Publish at 20 Hz (every 50ms) to keep the watchdog fed
        if now - last_pub >= 0.05:
            bridge.publish_twist(twist)
            last_pub = now
        time.sleep(0.005)
    bridge.publish_twist(Twist())
    time.sleep(_CMD_BUFFER)
    return not interrupted


@tool
def move_robot(command: str) -> str:
    """Send a short, precise movement command directly to the wheels via /cmd_vel.

    Use for fine adjustments — aligning, nudging, short scans.
    Do NOT use for room-to-room navigation — use navigate_to_pose() instead.

    Format — DIRECTION:VALUE  or  'S' to stop:
      F:20   forward 20 cm
      B:10   backward 10 cm
      L:90   rotate left 90 degrees
      R:45   rotate right 45 degrees
      S      stop immediately"""
    bridge = _bridge.get()
    bridge.clear_motion_stop()   # this is a deliberate move — start with a clean slate
    parts = command.upper().split(":")
    cmd = parts[0]
    val = float(parts[1]) if len(parts) > 1 else 0.0

    from geometry_msgs.msg import Twist
    twist = Twist()

    if cmd == "F":
        twist.linear.x = _LINEAR_VEL_MS
    elif cmd == "B":
        twist.linear.x = -_LINEAR_VEL_MS
    elif cmd == "L":
        twist.angular.z = _ANGULAR_VEL_RS
    elif cmd == "R":
        twist.angular.z = -_ANGULAR_VEL_RS
    elif cmd == "S":
        pass  # zero Twist = stop

    dur = _duration(cmd, val)
    if dur > 0:
        if not _drive_for_duration(bridge, twist, dur):
            return f"Movement interrupted: {command}"
    else:
        bridge.publish_twist(twist)

    return f"Movement done: {command}"


@tool
def navigate_to_pose(location: str) -> str:
    """Send the robot to a named location using Jetson Nav2 map-based navigation.

    Nav2 handles obstacle avoidance, path planning, and localisation automatically.
    Use for room-to-room or area navigation: 'kitchen', 'bedroom', 'entrance', etc.

    Returns immediately — the robot moves in the background. A system message will
    arrive when navigation completes or fails."""
    bridge = _bridge.get()

    known = bridge.get_known_locations()
    loc = location.lower().strip()

    if loc not in known:
        available = ", ".join(known.keys()) if known else "none configured yet"
        return f"Unknown location '{location}'. Available: {available}"

    x, y, yaw_deg = known[loc]
    bridge.start_nav_to_pose(x, y, yaw_deg, label=loc)
    return f"Navigation started: heading to '{location}' ({x:.1f}, {y:.1f}). I will report when I arrive."


# ── Visual-servoing approach ("go near the cup") ──────────────────────────────
# Uses the Jetson target_node (YOLOv8n) contract:
#   publish COCO class on /vision/target  →  read /vision/target_result JSON
#       {target, found, bearing_x[-1..1], rel_size, conf, stamp}
# We steer with /cmd_vel: turn toward bearing_x, drive forward until rel_size is
# big enough ("close"). Mono camera → no obstacle avoidance, no metric distance.
_APPROACH_STOP_REL_SIZE = 0.45   # arrived when the target box fills ~half the frame
_APPROACH_BEARING_DEADBAND = 0.15  # |bearing_x| below this = "centred enough" to drive
_APPROACH_TIMEOUT_S = 30.0       # hard cap on the whole approach
_APPROACH_TURN_GAIN = 1.6        # bearing_x → angular.z scale (clamped to _ANGULAR_VEL_RS)
_APPROACH_SCAN_GIVEUP_DEG = 400.0  # rotate up to ~full circle looking for the target
# target_node publishes at 5 Hz; a result older than this means the detector,
# camera, or Jetson link is gone — never steer (least of all drive forward) on it.
_APPROACH_RESULT_MAX_AGE_S = 1.5
# How long to hold still with NO fresh detection before giving up (covers both
# detector startup and a feed that died mid-approach).
_APPROACH_NO_RESULT_GIVEUP_S = 6.0


@tool
def navigate_to_visible_object(target: str) -> str:
    """Drive up to a visible object using the camera (no map needed).

    Visual servoing via the Jetson YOLOv8n target finder: the robot turns toward
    the named object and drives forward until it is close. Use for:
    "go near the cup", "approach the bottle", "go to that chair", "come to me".

    `target` must be a common object class (cup, bottle, chair, person, laptop,
    tv, book, …). For named rooms use navigate_to_pose() instead.

    This blocks while approaching (up to ~30 s) and returns when it arrives, loses
    the object, or times out. No obstacle avoidance — it drives straight at the
    target, so only use it with a clear path."""
    from geometry_msgs.msg import Twist

    bridge = _bridge.get()
    target = target.lower().strip()

    bridge.clear_motion_stop()                  # deliberate move — start with a clean slate
    bridge.set_vision_target(target)            # tell Jetson target_node to start hunting
    try:
        start = prev = time.time()
        scanned_deg = 0.0
        last_pub = 0.0
        no_result_since: float | None = None

        while True:
            now = time.time()
            # Real elapsed time since the previous iteration — the loop wakes
            # every ~5ms, so integrating a fixed per-iteration tick would count
            # rotation ~10x too fast and trip the scan give-up after ~40°.
            dt, prev = now - prev, now
            elapsed = now - start
            if bridge.motion_interrupted():
                return f"Stopped approaching the {target}."
            if elapsed > _APPROACH_TIMEOUT_S:
                return f"Approach timed out after {int(_APPROACH_TIMEOUT_S)}s before reaching the {target}."

            # Fresh detections only: a stale cached result (detector/camera/link
            # down) must not keep steering the wheels — especially not forward.
            result = bridge.get_target_result(max_age_s=_APPROACH_RESULT_MAX_AGE_S)
            twist = Twist()

            if result is None:
                # No fresh detection — hold still (zero twist) rather than move
                # blind; give up honestly if the feed stays silent.
                if no_result_since is None:
                    no_result_since = now
                if now - no_result_since > _APPROACH_NO_RESULT_GIVEUP_S:
                    return (f"I stopped — I'm not getting anything from my camera, "
                            f"so I can't safely approach the {target}.")
            elif not result.get("found"):
                # Target not in frame — rotate in place to search.
                no_result_since = None
                twist.angular.z = _ANGULAR_VEL_RS
                scanned_deg += math.degrees(_STEADY_STATE_ANGULAR_VEL) * dt
                if scanned_deg > _APPROACH_SCAN_GIVEUP_DEG:
                    return f"I scanned all the way around but couldn't find the {target}."
            else:
                no_result_since = None
                scanned_deg = 0.0               # found it — reset the search sweep
                rel_size = float(result.get("rel_size", 0.0))
                bearing_x = float(result.get("bearing_x", 0.0))

                if rel_size >= _APPROACH_STOP_REL_SIZE:
                    return f"I've reached the {target}."
                elif abs(bearing_x) > _APPROACH_BEARING_DEADBAND:
                    # Turn toward it. bearing_x>0 = right → angular.z negative (CCW+).
                    az = max(-_ANGULAR_VEL_RS, min(_ANGULAR_VEL_RS, -bearing_x * _APPROACH_TURN_GAIN * _ANGULAR_VEL_RS))
                    twist.angular.z = az
                else:
                    twist.linear.x = _LINEAR_VEL_MS   # centred → drive forward

            if now - last_pub >= 0.05:
                bridge.publish_twist(twist)
                last_pub = now
            time.sleep(0.005)
    finally:
        bridge.publish_twist(Twist())           # always stop the wheels
        bridge.set_vision_target("")            # idle the Jetson target_node


# NOTE: depth-based approach (Jetson /vision/find_object_pose service → Nav2) is
# deferred until the D555 depth camera + Isaac ROS SLAM/Nav2 stack lands. When it
# does, prefer it for obstacle-aware approach and fall back to the visual servoing
# above. See ARCHITECTURE.md "Build Order" (Phase 4).
