import json
import time

from langchain_core.tools import tool

from . import _bridge

# Must match chassis_pilot parameters
_FWD_CM_S    = 12.0
_TURN_DEG_S  = 180.0
_CMD_BUFFER  = 0.3   # extra sleep after each command (seconds)


def _duration(cmd: str, val: float) -> float:
    if cmd in ("F", "B"):
        return val / _FWD_CM_S + _CMD_BUFFER
    if cmd in ("L", "R"):
        return val / _TURN_DEG_S + _CMD_BUFFER
    return 0.0


@tool
def move_robot(command: str) -> str:
    """Send a timed movement command to the robot chassis and block until done.

    Format — DIRECTION:VALUE  or  'S' to stop immediately:
      F:30   forward 30 cm
      B:20   backward 20 cm
      L:90   rotate left 90 degrees
      R:45   rotate right 45 degrees
      S      stop"""
    bridge = _bridge.get()
    bridge.publish_movement(command)
    parts = command.upper().split(":")
    cmd   = parts[0]
    val   = float(parts[1]) if len(parts) > 1 else 0.0
    dur   = _duration(cmd, val)
    if dur > 0:
        time.sleep(dur)
    return f"Movement done: {command}"


@tool
def navigate_to(target: str) -> str:
    """Autonomously navigate the robot to a named object.

    Phase 1 — Scan: rotate 45° at a time (max 360°) using YOLO + Moondream VLM
               to find the target.
    Phase 2 — Approach: align left/right, step forward 20 cm, check proximity.
               Repeat up to 6 times.

    Use for 'go to X', 'find and approach X', 'bring yourself near X'."""
    bridge = _bridge.get()
    bridge.publish_speech(f"Looking for the {target}, scanning around.")

    # ── Phase 1: Scan 360° ─────────────────────────────────────────────────
    direction: str | None  = None
    distance:  float | None = None

    for step in range(8):  # 8 × 45° = 360°

        # Fast path: YOLO detections (no VLM roundtrip)
        try:
            objects = json.loads(bridge.get_objects_json())
        except Exception:
            objects = []

        for obj in objects:
            if target.lower() in obj.get("class", "").lower():
                direction = obj.get("direction", "center")
                distance  = obj.get("distance_m")
                break

        if direction:
            break

        # Slow path: ask Moondream VLM
        answer = bridge.query_vision(
            f"Do you see a {target}? "
            f"If yes reply: YES <left|center|right> <metres>. "
            f"If no reply: NO."
        )
        ans = answer.lower()
        if ans.startswith("yes"):
            parts = ans.split()
            direction = "center"
            for word in parts:
                if word in ("left", "center", "right"):
                    direction = word
                    break
            for word in parts:
                try:
                    distance = float(word.rstrip("m"))
                    break
                except ValueError:
                    pass
            break

        bridge.publish_speech(f"Not found yet, rotating… ({step + 1}/8)")
        bridge.publish_movement("L:45")
        time.sleep(_duration("L", 45))

    if direction is None:
        bridge.publish_speech(f"I couldn't find the {target} after a full scan.")
        return f"Navigation failed: {target} not found after 360° scan."

    bridge.publish_speech(f"Found the {target}, approaching now.")

    # ── Phase 2: Approach ──────────────────────────────────────────────────
    for _ in range(6):  # max 6 × 20 cm = 1.2 m

        if direction == "left":
            bridge.publish_movement("L:20")
            time.sleep(_duration("L", 20))
        elif direction == "right":
            bridge.publish_movement("R:20")
            time.sleep(_duration("R", 20))

        bridge.publish_movement("F:20")
        time.sleep(_duration("F", 20))

        close = bridge.query_vision(
            f"Am I now close to the {target} (within 30 cm)? Reply YES or NO."
        )
        if "yes" in close.lower():
            break

        # Re-detect for next alignment
        try:
            objects = json.loads(bridge.get_objects_json())
        except Exception:
            objects = []
        direction = "center"
        for obj in objects:
            if target.lower() in obj.get("class", "").lower():
                direction = obj.get("direction", "center")
                break

    bridge.publish_speech(f"I've reached the {target}.")
    return f"Navigation complete: reached the {target}."
