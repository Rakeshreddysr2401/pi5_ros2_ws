import math
import os
import time
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from . import _bridge

# ── Fine-movement Twist parameters (direct /cmd_vel, bypasses Nav2) ──────────
#
# UNITS. rover_firmware_v2.ino runs a closed-loop PI controller on each SIDE's
# measured wheel velocity in SI m/s: it computes wL/wR = vx -/+ wz*0.34/2 and
# PID-tracks them against the encoders. /cmd_vel is therefore genuinely SI — a command
# of 0.20 m/s produces ~0.20 m/s, not "some PWM fraction".
#
# The constants here previously encoded the OPPOSITE assumption, inherited from
# an older open-loop firmware: 0.28 m/s "= ~93% PWM" and a claimed physical
# speed of 0.60 m/s. Against the closed-loop firmware that made every distance
# wrong by the ratio of the two — move_robot("F:20") computed
# 0.20/0.60 = 0.33 s of drive, which at the ACTUAL 0.28 m/s covers 9 cm, not
# 20 cm. The rover repo measures the truth directly (OPERATIONS.md §2, the
# teleop speed table): "forward / back | 0.20 m/s | 20 cm/s".
#
# TURN RATE — read this before changing it. The rover repo has measured this
# chassis twice and the two numbers disagree, on purpose:
#   * OPERATIONS.md §2  — a 2.0 rad/s pivot puts each wheel at 34 cm/s, which
#     is OVER cuVSLAM's tracking limit: "expect jumps".
#   * teleop_web.py     — at 2.0 rad/s (≈47% duty) the four tyres cannot break
#     loose sideways at all, so a pivot command turns into a forward/backward
#     CURVE. That is how an operator drove 10.7 m of "room loop" inside a 1.8 m
#     box (2026-08-22). Teleop's fix was to raise its pivot to 5.0 rad/s, just
#     under the 5.06 rad/s full authority, where both wheels reach opposite
#     full duty and it pivots cleanly.
# 2.8 rad/s sat in the worst band between the two: fast enough to disturb
# cuVSLAM, too slow to actually pivot. The default now matches teleop's
# proven-clean pivot, because a turn that silently curves is unbounded error
# while a VO jump is at least visible in vo_z.
#
# EVERY value below is env-overridable so it can be re-calibrated on the robot
# without a rebuild (logs/calibrate_rotation.py and `./rover compare` in the
# rover repo produce the numbers).
def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Commanded linear velocity, m/s. Keep at/below the rover's 0.22 m/s cap.
_LINEAR_VEL_MS = _envf("LANGROBO_LINEAR_VEL_MS", 0.20)
# Physical speed achieved at that command. Equal to the command because the
# firmware closed-loop tracks it; re-measure with a tape if the PID is retuned.
_PHYSICAL_VEL_MS = _envf("LANGROBO_PHYSICAL_VEL_MS", 0.20)
# Commanded yaw rate, rad/s. 5.0 = teleop's clean pivot (see above).
_ANGULAR_VEL_RS = _envf("LANGROBO_ANGULAR_VEL_RS", 5.0)
# Yaw rate actually achieved at that command. Defaults to the command: the
# firmware tracks WHEEL velocity exactly, and the wheel→yaw conversion is only
# exact with no sideways scrub — which is precisely the regime a full-duty
# pivot is chosen for. MEASURE IT (logs/calibrate_rotation.py) and set
# LANGROBO_STEADY_ANGULAR_VEL if turns overshoot or undershoot.
_STEADY_STATE_ANGULAR_VEL = _envf("LANGROBO_STEADY_ANGULAR_VEL", _ANGULAR_VEL_RS)
_TURN_STARTUP_DELAY = _envf("LANGROBO_TURN_STARTUP_S", 0.0)  # ramp-up offset, s
_CMD_BUFFER = 0.2    # extra sleep after each command (seconds)


# ── Pan/tilt camera head — OFF by default, because it does not exist ────────
#
# set_pan_tilt() publishes /servo_pan and /servo_tilt. Nothing subscribes:
# rover_firmware_v2.ino declares three subscriptions (/cmd_vel, /pid_gains,
# /reset_odom) and no servos at all, and the micro-ROS entity caps are the
# reason it is that short. So every head movement was a no-op that still cost
# real time — ensure_head_centred() slept 0.8s and approach.py's search swept
# three pan angles at 1.6s each, ~5.6s of dead air before every single
# approach_object call, on hardware that cannot move.
#
# Set LANGROBO_PAN_TILT=1 once servos are wired and the firmware subscribes.
# While it is off the pose-corruption problem the sweep guards against cannot
# happen either — a head that never moves never skews the base pose.
PAN_TILT_ENABLED = os.environ.get("LANGROBO_PAN_TILT", "0").strip().lower() not in (
    "", "0", "false", "no", "off")


# Camera-head recovery: vSLAM tracks the CAMERA and absorbs a head pan as
# apparent base rotation (rigid-extrinsic design — JETSON_D555_SETUP.md §3),
# so while the head is off-centre the robot's own map pose reads rotated.
# Anything that reads get_current_pose or sends a map goal must go through
# ensure_head_centred first: re-centre the servos, then give the servo travel
# + a few vSLAM frames to re-track before trusting the pose.
_RECENTER_SETTLE_S = 0.8


def ensure_head_centred(bridge) -> None:
    """Re-centre the camera head (if panned/tilted) and wait for the base
    pose to become trustworthy again. No-op when already centred, and no-op
    entirely when there is no pan/tilt hardware (see PAN_TILT_ENABLED)."""
    if not PAN_TILT_ENABLED:
        return
    pan, tilt = bridge.get_pan_tilt()
    if abs(pan) > 1.0 or abs(tilt) > 1.0:
        bridge.set_pan_tilt(0.0, 0.0)
        time.sleep(_RECENTER_SETTLE_S)


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


# Origin of the most recent navigate_to_pose request. The Nav2 result lands
# minutes later as a [SYSTEM] turn whose default reply sink is the speaker;
# when the request came from Telegram the completion report must be routed
# back to that chat instead — agent_node._on_nav_done reads this to build
# the routing hint (same pattern as errand-reply forwarding).
_last_nav_requester: dict | None = None


def get_last_nav_requester() -> dict | None:
    return _last_nav_requester


@tool
def navigate_to_pose(location: str,
                     state: Annotated[dict, InjectedState]) -> str:
    """Send the robot to a named location using Jetson Nav2 map-based navigation.

    Nav2 handles obstacle avoidance, path planning, and localisation automatically.
    Use for room-to-room or area navigation: 'kitchen', 'bedroom', 'entrance', etc.

    Returns immediately — the robot moves in the background. A system message will
    arrive when navigation completes or fails."""
    bridge = _bridge.get()
    ensure_head_centred(bridge)   # a panned head skews Nav2's start pose

    known = bridge.get_known_locations()
    loc = location.lower().strip()

    if loc not in known:
        available = ", ".join(known.keys()) if known else "none configured yet"
        return f"Unknown location '{location}'. Available: {available}"

    global _last_nav_requester
    _last_nav_requester = {
        "channel": state.get("channel") or "voice",
        "sender": state.get("sender_name") or "voice",
    }

    x, y, yaw_deg = known[loc]
    bridge.start_nav_to_pose(x, y, yaw_deg, label=loc)
    return f"Navigation started: heading to '{location}' ({x:.1f}, {y:.1f}). I will report when I arrive."


@tool
def save_location(name: str) -> str:
    """Save the robot's CURRENT position under a name, so the user can send the
    robot back later with navigate_to_pose(name). Use when the user says
    "remember this spot as X", "save this location as the charging dock", etc.
    Survives restarts.

    name: short lowercase identifier, e.g. 'table_5' or 'charging_dock'."""
    bridge = _bridge.get()
    ensure_head_centred(bridge)   # a panned head would save a rotated pose
    pose = bridge.get_current_pose()
    if pose is None:
        return ("I can't determine my position right now — localisation isn't "
                "giving me a fix, so I can't save this spot.")
    x, y, yaw = pose
    key = name.lower().strip().replace(" ", "_")
    bridge.add_known_location(key, round(x, 2), round(y, 2), round(yaw, 1))
    return (f"Saved my current position ({x:.2f}, {y:.2f}, facing {yaw:.0f}°) as "
            f"'{key}'. Say the word and I can navigate back to it anytime.")


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
    "go near the cup", "approach the bottle", "go to that chair".

    `target` must be a common object class (cup, bottle, chair, laptop, tv,
    book, …). Do NOT use it for people — person following/approach is not
    available until the depth camera lands (say so honestly). For named rooms
    use navigate_to_pose() instead.

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


# Depth-based approach lives in tools/approach.py (approach_object,
# scan_surroundings) — the D555 + Isaac ROS pipeline turns detections into
# Nav2 goals; navigate_to_visible_object above stays the no-map mono fallback.

_PAN_MIN_DEG, _PAN_MAX_DEG = -90.0, 90.0
_TILT_MIN_DEG, _TILT_MAX_DEG = -30.0, 30.0


@tool
def point_camera(pan_deg: float = 0.0, tilt_deg: float = 0.0) -> str:
    """Point the robot's camera using its pan-tilt mount (2 servos).

    pan_deg: horizontal angle, -90 (full left) .. 90 (full right), 0 = forward.
    tilt_deg: vertical angle, -30 (down) .. 30 (up), 0 = level.

    Use for "look left/right/up/down", "look at the door", or to sweep the
    camera without moving the wheels. Drives the ESP32 pan/tilt servos
    (/servo_pan, /servo_tilt); if the mount isn't installed yet nothing moves —
    say so rather than claiming it worked."""
    if not PAN_TILT_ENABLED:
        # Say so instead of reporting a move that physically cannot happen —
        # the docstring already promises this, but the code used to claim
        # success regardless (see PAN_TILT_ENABLED for why nothing listens).
        return ("I don't have a pan-tilt camera mount fitted, so I can't turn "
                "my head. Tell the user, and offer to turn the whole robot "
                "instead.")
    pan = max(_PAN_MIN_DEG, min(_PAN_MAX_DEG, pan_deg))
    tilt = max(_TILT_MIN_DEG, min(_TILT_MAX_DEG, tilt_deg))
    clamped = (pan != pan_deg) or (tilt != tilt_deg)
    _bridge.get().set_pan_tilt(pan, tilt)
    note = " (clamped to valid range)" if clamped else ""
    return f"Camera pointed: pan={pan:.0f}°, tilt={tilt:.0f}°{note}"
