import math
import time

from langchain_core.tools import tool

from . import _bridge

# Fine-movement Twist parameters (direct /cmd_vel, bypasses Nav2)
_LINEAR_VEL_MS  = 0.12   # m/s forward/backward
_ANGULAR_VEL_RS = 1.2    # rad/s rotation
_CMD_BUFFER     = 0.2    # extra sleep after each command (seconds)


def _duration(cmd: str, val: float) -> float:
    if cmd in ("F", "B"):
        return (val / 100.0) / _LINEAR_VEL_MS + _CMD_BUFFER   # cm → m
    if cmd in ("L", "R"):
        return math.radians(val) / _ANGULAR_VEL_RS + _CMD_BUFFER
    return 0.0


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

    bridge.publish_twist(twist)
    dur = _duration(cmd, val)
    if dur > 0:
        time.sleep(dur)
        bridge.publish_twist(Twist())  # stop after duration

    return f"Movement done: {command}"


@tool
def navigate_to_pose(location: str) -> str:
    """Send the robot to a named location using Jetson Nav2 map-based navigation.

    Nav2 handles obstacle avoidance, path planning, and localisation automatically.
    Use for room-to-room or area navigation: 'kitchen', 'bedroom', 'entrance', etc.

    The robot will navigate autonomously — call speak() first to acknowledge the user."""
    bridge = _bridge.get()

    known = bridge.get_known_locations()
    loc = location.lower().strip()

    if loc not in known:
        available = ", ".join(known.keys()) if known else "none configured yet"
        return f"Unknown location '{location}'. Available: {available}"

    x, y, yaw_deg = known[loc]
    bridge.publish_goal_pose(x, y, yaw_deg)
    return f"Nav2 goal sent: navigating to '{location}' ({x:.1f}, {y:.1f})"


@tool
def navigate_to_object(target: str) -> str:
    """Scan for a named object and approach it using Moondream VLM + fine movement.

    Use when the object is not on the map — e.g. 'the blue bottle', 'the person'.
    Performs a 360° scan then approaches step by step.

    For named rooms or map locations use navigate_to_pose() instead."""
    bridge = _bridge.get()
    bridge.publish_speech(f"Looking for the {target}, scanning around.")

    from geometry_msgs.msg import Twist

    direction = None

    # Phase 1: Scan 360° via Moondream VLM
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
        bridge.publish_twist(twist)
        time.sleep(_duration("L", 45))
        bridge.publish_twist(Twist())

    if direction is None:
        bridge.publish_speech(f"I couldn't find the {target} after a full scan.")
        return f"Navigation failed: {target} not found after 360° scan."

    bridge.publish_speech(f"Found the {target}, approaching now.")

    # Phase 2: Approach step by step
    for _ in range(6):  # max 6 × 20 cm = 1.2 m
        if direction == "left":
            twist = Twist()
            twist.angular.z = _ANGULAR_VEL_RS
            bridge.publish_twist(twist)
            time.sleep(_duration("L", 20))
            bridge.publish_twist(Twist())
        elif direction == "right":
            twist = Twist()
            twist.angular.z = -_ANGULAR_VEL_RS
            bridge.publish_twist(twist)
            time.sleep(_duration("R", 20))
            bridge.publish_twist(Twist())

        fwd = Twist()
        fwd.linear.x = _LINEAR_VEL_MS
        bridge.publish_twist(fwd)
        time.sleep(_duration("F", 20))
        bridge.publish_twist(Twist())

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
