import math
import time

from langchain_core.tools import tool

from . import _bridge

# Fine-movement Twist parameters (direct /cmd_vel, bypasses Nav2)
_LINEAR_VEL_MS  = 0.28   # m/s forward/backward
_ANGULAR_VEL_RS = 1.2    # rad/s rotation
_CMD_BUFFER     = 0.2    # extra sleep after each command (seconds)


def _duration(cmd: str, val: float) -> float:
    if cmd in ("F", "B"):
        return (val / 100.0) / _LINEAR_VEL_MS + _CMD_BUFFER   # cm → m
    if cmd in ("L", "R"):
        return math.radians(val) / _ANGULAR_VEL_RS + _CMD_BUFFER
    return 0.0


def _drive_for_duration(bridge, twist, dur: float) -> None:
    """Publish twist at 10 Hz for `dur` seconds, then send a stop.

    Keeps the ESP32 watchdog (500 ms) fed throughout the move."""
    from geometry_msgs.msg import Twist
    end = time.time() + dur
    while time.time() < end:
        bridge.publish_twist(twist)
        time.sleep(0.1)
    bridge.publish_twist(Twist())


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
        _drive_for_duration(bridge, twist, dur)
    else:
        bridge.publish_twist(twist)

    return f"Movement done: {command}"


@tool
def navigate_to_pose(location: str) -> str:
    """Send the robot to a named location using Jetson Nav2 map-based navigation.

    Nav2 handles obstacle avoidance, path planning, and localisation automatically.
    Use for room-to-room or area navigation: 'kitchen', 'bedroom', 'entrance', etc.

    Returns immediately — the robot moves in the background. A system message will
    arrive when navigation completes or fails. Call speak() first to acknowledge."""
    bridge = _bridge.get()

    known = bridge.get_known_locations()
    loc = location.lower().strip()

    if loc not in known:
        available = ", ".join(known.keys()) if known else "none configured yet"
        return f"Unknown location '{location}'. Available: {available}"

    x, y, yaw_deg = known[loc]
    bridge.start_nav_to_pose(x, y, yaw_deg, label=loc)
    return f"Navigation started: heading to '{location}' ({x:.1f}, {y:.1f}). I will report when I arrive."


@tool
def navigate_to_visible_object(target: str) -> str:
    """Find a visible object using the Jetson depth camera + VLM, then navigate to it via Nav2.

    This is the preferred way to approach objects seen in the camera feed:
    1. Calls the Jetson /vision/find_object_pose service (VLM bbox + RealSense depth → 3D pose)
    2. Passes the pose to Nav2 for obstacle-aware navigation

    Use when: "go near the chair", "approach the bottle", "go to that person"
    Falls back to navigate_to_object() if the Jetson service is unavailable.

    Returns immediately — navigation runs in background."""
    bridge = _bridge.get()

    try:
        from robot_interfaces.srv import FindObjectPose
        req = FindObjectPose.Request()
        req.object_description = target
        resp = bridge.call_service("/vision/find_object_pose", FindObjectPose, req, timeout=15.0)

        if not resp.found:
            reason = resp.reason or "object not visible in camera"
            bridge.publish_speech(f"I couldn't find the {target} — {reason}. Let me try scanning.")
            return _fallback_scan(target, bridge)

        pose = resp.pose
        x = pose.pose.position.x
        y = pose.pose.position.y

        import math as _math
        q = pose.pose.orientation
        yaw_rad = _math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        yaw_deg = _math.degrees(yaw_rad)

        bridge.start_nav_to_pose(x, y, yaw_deg, label=target)
        return f"Found {target} in camera view. Navigating to it via Nav2. I'll report when I arrive."

    except TimeoutError:
        bridge.publish_speech(f"Jetson vision service unavailable. Scanning for {target} manually.")
        return _fallback_scan(target, bridge)
    except Exception as e:
        bridge.publish_speech(f"Vision service error: {e}. Trying manual scan.")
        return _fallback_scan(target, bridge)


def _fallback_scan(target: str, bridge) -> str:
    """VLM 360° scan + direct approach — used when Jetson service is unavailable."""
    from geometry_msgs.msg import Twist

    bridge.publish_speech(f"Looking for the {target}, scanning around.")
    direction = None

    for step in range(8):  # 8 × 45° = 360°
        answer = bridge.query_vision(
            f"Do you see a {target}? "
            f"If yes reply: YES <left|centre|right> <metres>. "
            f"If no reply: NO."
        )
        ans = answer.lower()
        if ans.startswith("yes"):
            parts = ans.split()
            direction = "centre"
            for word in parts:
                if word in ("left", "centre", "center", "right"):
                    direction = word.replace("center", "centre")
                    break
            break

        bridge.publish_speech(f"Not found yet, rotating… ({step + 1}/8)")
        twist = Twist()
        twist.angular.z = _ANGULAR_VEL_RS
        _drive_for_duration(bridge, twist, _duration("L", 45))

    if direction is None:
        bridge.publish_speech(f"I couldn't find the {target} after a full scan.")
        return f"Navigation failed: {target} not found after 360° scan."

    bridge.publish_speech(f"Found the {target}, approaching now.")

    for _ in range(6):  # max 6 × 20 cm = 1.2 m
        if direction == "left":
            twist = Twist()
            twist.angular.z = _ANGULAR_VEL_RS
            _drive_for_duration(bridge, twist, _duration("L", 20))
        elif direction == "right":
            twist = Twist()
            twist.angular.z = -_ANGULAR_VEL_RS
            _drive_for_duration(bridge, twist, _duration("R", 20))

        fwd = Twist()
        fwd.linear.x = _LINEAR_VEL_MS
        _drive_for_duration(bridge, fwd, _duration("F", 20))

        close = bridge.query_vision(
            f"Am I now close to the {target} (within 30 cm)? Reply YES or NO."
        )
        if "yes" in close.lower():
            break

        answer = bridge.query_vision(
            f"Where is the {target} now — left, centre, or right?"
        )
        ans = answer.lower()
        if "left" in ans:
            direction = "left"
        elif "right" in ans:
            direction = "right"
        else:
            direction = "centre"

    bridge.publish_speech(f"I've reached the {target}.")
    return f"Navigation complete: reached the {target}."


@tool
def navigate_to_object(target: str) -> str:
    """Fallback: scan 360° using Moondream VLM and approach object with direct wheel control.

    Use only if navigate_to_visible_object() fails or the Jetson service is unavailable.
    No obstacle avoidance — drives directly toward detected object.

    For named rooms use navigate_to_pose(). For objects with Jetson running use navigate_to_visible_object()."""
    bridge = _bridge.get()
    return _fallback_scan(target, bridge)
