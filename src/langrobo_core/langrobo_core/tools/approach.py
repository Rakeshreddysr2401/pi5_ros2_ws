"""Depth-camera object approach — "go near the chair", "come to me".

Built on the D555 + Isaac ROS pipeline (JETSON_D555_SETUP.md): the Jetson
publishes map-frame 3D object positions on /vision/detections_3d, and the
brain turns them into Nav2 goals — obstacle-aware, metric, person included
(unlike the mono navigate_to_visible_object servo loop, which stays as the
no-map fallback).

Search behaviour when the target isn't in view (New_Requirement goal 3):
  1. pan the camera head left/right (servos — fast, wheels never move)
  2. rotate the base in steps, checking detections after each
  3. give up honestly after a full circle

All geometry is pure and unit-tested; robot I/O goes through _bridge.get().
"""

import math
import os
import time
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from . import _bridge
from . import movement as _mv

# How close the robot parks from the object's map position (metres). People
# get more personal space than furniture. Floor: D555 depth goes blind under
# ~0.4 m, and Nav2 can stop up to xy_goal_tolerance (0.20 m) short of the
# goal — don't set the object standoff below ~0.4 or the camera loses the
# target it just approached. Env-tunable without a code change.
_STANDOFF_M = float(os.environ.get("LANGROBO_STANDOFF_M", "0.45"))
_STANDOFF_PERSON_M = float(os.environ.get("LANGROBO_STANDOFF_PERSON_M", "0.8"))
# A detection older than this is "not in view" — trigger the search.
_FRESH_DETECTION_S = 3.0
# Camera-head sweep angles (D555 HFOV ≈ 87°, so ±55° pan covers ≈ ±98°).
_PAN_SWEEP_DEG = (0.0, -55.0, 55.0)
_PAN_DWELL_S = 1.6          # settle + let the detector publish at each stop
_BASE_STEP_DEG = 60.0       # base-rotation search step
_BASE_STEPS = 6             # full circle
_BASE_DWELL_S = 1.4


def compute_standoff_goal(rx: float, ry: float, ox: float, oy: float,
                          standoff: float) -> tuple:
    """Nav2 goal (gx, gy, yaw_deg) that parks `standoff` metres short of the
    object (ox, oy) on the robot→object line, facing the object.

    If the robot is already inside the standoff ring, the goal is the robot's
    own position with the yaw turned to face the object (Nav2 rotates in
    place)."""
    dx, dy = ox - rx, oy - ry
    dist = math.hypot(dx, dy)
    yaw_deg = math.degrees(math.atan2(dy, dx))
    if dist <= standoff or dist < 1e-6:
        return (rx, ry, yaw_deg)
    scale = (dist - standoff) / dist
    return (rx + dx * scale, ry + dy * scale, yaw_deg)


def _fresh(bridge, target: str) -> dict | None:
    return bridge.get_detected_object(target, max_age_s=_FRESH_DETECTION_S)


def _dwell_for_detection(bridge, target: str, dwell_s: float) -> dict | None:
    """Wait up to dwell_s for a fresh detection, polling; abort on interrupt."""
    end = time.time() + dwell_s
    while time.time() < end:
        if bridge.motion_interrupted():
            return None
        det = _fresh(bridge, target)
        if det is not None:
            return det
        time.sleep(0.15)
    return None


def _search(bridge, target: str) -> dict | None:
    """Look for `target`: camera pan sweep first, then base rotation.

    Returns a fresh detection dict or None (not found / interrupted). Always
    re-centres the camera head before returning."""
    try:
        for pan in _PAN_SWEEP_DEG:
            if bridge.motion_interrupted():
                return None
            bridge.set_pan_tilt(pan, 0.0)
            det = _dwell_for_detection(bridge, target, _PAN_DWELL_S)
            if det is not None:
                return det
    finally:
        # Re-centre and wait for vSLAM to re-track before anyone reads the
        # base pose — mid-pan (and briefly after) map→base_link is rotated by
        # the head angle. The detection's map coordinates are already correct
        # (captured through map→camera, which vSLAM tracks directly).
        _mv.ensure_head_centred(bridge)

    # Base rotation — one step at a time, checking after each. Uses the same
    # calibrated timed-twist drive as move_robot.
    try:
        from geometry_msgs.msg import Twist
    except ImportError:
        return None       # no ROS2 msgs (Studio/tests) — pan sweep was all we had
    twist = Twist()
    twist.angular.z = _mv._ANGULAR_VEL_RS
    step_dur = _mv._duration("L", _BASE_STEP_DEG)
    for _ in range(_BASE_STEPS):
        if bridge.motion_interrupted():
            return None
        if not _mv._drive_for_duration(bridge, twist, step_dur):
            return None
        det = _dwell_for_detection(bridge, target, _BASE_DWELL_S)
        if det is not None:
            return det
    return None


@tool
def approach_object(target: str,
                    state: Annotated[dict, InjectedState]) -> str:
    """Find a person or object with the depth camera and drive up close to it,
    avoiding obstacles (Nav2). THE tool for "come here", "come to me",
    "go near the chair", "go to the sofa" — any approach to something seen by
    the camera rather than a saved map location.

    If the target isn't currently in view the robot searches for it: camera
    head sweep left/right first, then rotating the base a full circle.

    target: a common object class in lowercase English — 'person', 'chair',
    'couch', 'dining table', 'tv', 'bottle', 'cup', 'laptop', ...
    For saved/named places use navigate_to_pose() instead.

    Returns quickly once the target is found — the drive continues in the
    background and a system message reports arrival or failure."""
    bridge = _bridge.get()
    target = target.lower().strip()
    who = "you" if target == "person" else f"the {target}"

    bridge.clear_motion_stop()

    det = _fresh(bridge, target)
    if det is None:
        det = _search(bridge, target)
        if det is None:
            if bridge.motion_interrupted():
                return f"Stopped searching for {who}."
            # World-model fallback: drive to where it was LAST seen.
            last = bridge.get_last_seen_object(target)
            if last is not None:
                det = last
            else:
                return (f"I looked around — camera sweep and a full turn — but "
                        f"I couldn't find {who}. I haven't seen one before "
                        f"either, so I have nowhere to go.")

    # If the head is still panned (e.g. "look left" then "come here"), the
    # base pose reads rotated — centre it first, THEN localise.
    _mv.ensure_head_centred(bridge)
    pose = bridge.get_current_pose()
    if pose is None:
        return (f"I can see {who}, but localisation isn't giving me my own "
                f"position, so I can't plan a safe path right now.")

    rx, ry, _ = pose
    standoff = _STANDOFF_PERSON_M if target == "person" else _STANDOFF_M
    gx, gy, yaw = compute_standoff_goal(rx, ry, det["x"], det["y"], standoff)

    # Nav completion arrives minutes later as a [SYSTEM] turn — remember who
    # asked so a Telegram-initiated approach reports back to that chat.
    _mv._last_nav_requester = {
        "channel": state.get("channel") or "voice",
        "sender": state.get("sender_name") or "voice",
    }
    label = "you" if target == "person" else f"near the {target}"
    bridge.start_nav_to_pose(round(gx, 2), round(gy, 2), round(yaw, 1), label=label)

    stale = det.get("age_s", 0.0) > _FRESH_DETECTION_S
    seen = ("I remember where it was" if stale else
            f"I can see {who}" if target != "person" else "I see you")
    return (f"{seen} — on my way ({math.hypot(det['x'] - rx, det['y'] - ry):.1f} m). "
            f"I'll say when I'm there.")


# ── VLM pixel-grounding approach (arbitrary described objects) ───────────────
# For things YOLO has no class for ("surf excel packet", "the red mug"): the
# VLM points at the object in the look frame (normalized coords), the Jetson
# pixel_to_goal node deprojects that pixel with real depth into a map-frame
# Nav2 goal (standoff already applied). Search = rotate the base in 90° steps,
# one VLM check per orientation — each check is an LLM round-trip (~10-40 s),
# so the sweep is bounded at a full circle.

_VLM_LOCATE_PROMPT = (
    'Look at this image. Find: "{description}". '
    "Reply with ONLY a JSON object, no other text: "
    '{{"found": true, "x": N, "y": N}} or {{"found": false}}. '
    "x and y are the CENTER of the object in normalized image coordinates "
    "from 0 to 1000, where (0,0) is the top-left corner."
)


def _vlm_locate(frame: bytes, description: str) -> tuple[float, float] | None:
    """Ask the multimodal LLM where `description` is in the JPEG frame.
    Returns color-image pixel (u, v) or None (not found / unparseable)."""
    import base64
    import io
    import json as _json
    import re

    from langchain_core.messages import HumanMessage
    from PIL import Image

    from ..services.llm import get_llm

    width, height = Image.open(io.BytesIO(frame)).size
    b64 = base64.b64encode(frame).decode()
    reply = get_llm("local_agent").invoke([HumanMessage(content=[
        {"type": "text", "text": _VLM_LOCATE_PROMPT.format(description=description)},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ])])
    m = re.search(r"\{.*\}", str(reply.content), re.DOTALL)
    if not m:
        return None
    try:
        data = _json.loads(m.group(0))
    except _json.JSONDecodeError:
        return None
    if not data.get("found"):
        return None
    try:
        x, y = float(data["x"]), float(data["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        return None
    return (x / 1000.0 * width, y / 1000.0 * height)


def _fresh_frame(bridge, settle_s: float = 2.5) -> bytes | None:
    """Frame captured AFTER now — the 2 Hz look feed needs a beat to publish
    a post-motion view; a pre-rotation cache hit would re-check the old view."""
    end = time.time() + settle_s
    while time.time() < end:
        if bridge.motion_interrupted():
            return None
        frame = bridge.get_frame(max_age_s=0.8)
        if frame is not None:
            return frame
        time.sleep(0.15)
    return bridge.get_frame(max_age_s=10.0)   # degraded fallback: newest we have


@tool
def approach_described_object(description: str,
                              state: Annotated[dict, InjectedState]) -> str:
    """Find ANY described object with the camera and drive up close to it,
    avoiding obstacles (Nav2). Use for objects YOLO has no class for —
    brands, specific or unusual items: "the surf excel detergent packet",
    "the red coffee mug", "my black backpack". For common object classes
    (person, chair, cup, ...) approach_object is faster — prefer it.

    If the object isn't in the current view the robot turns in 90° steps and
    re-checks, up to a full circle (each check takes a while — the vision
    model looks at a fresh photo every step).

    Returns once the object is found and the drive starts — the drive
    continues in the background and a system message reports arrival."""
    bridge = _bridge.get()
    description = description.strip()
    bridge.clear_motion_stop()
    _mv.ensure_head_centred(bridge)

    try:
        from geometry_msgs.msg import Twist
    except ImportError:
        Twist = None                    # Studio/tests — forward check only

    uv = None
    # Orientation 0 = current view; then up to 4 × 90° rotations (full circle
    # + a recheck of the start orientation in case odometry under-rotates).
    for step in range(5):
        if bridge.motion_interrupted():
            return f"Stopped searching for the {description}."
        if step > 0:
            if Twist is None:
                break
            twist = Twist()
            twist.angular.z = _mv._ANGULAR_VEL_RS
            if not _mv._drive_for_duration(bridge, twist, _mv._duration("L", 90.0)):
                return f"Stopped searching for the {description}."
        frame = _fresh_frame(bridge)
        if frame is None:
            if bridge.motion_interrupted():
                return f"Stopped searching for the {description}."
            return ("My camera feed isn't giving me a fresh image right now, "
                    "so I can't look for it.")
        try:
            uv = _vlm_locate(frame, description)
        except Exception as e:
            return (f"I couldn't analyse the camera image (vision model error: "
                    f"{type(e).__name__}). Try again in a moment.")
        if uv is not None:
            break

    if uv is None:
        return (f"I turned a full circle and looked carefully, but I couldn't "
                f"spot the {description} anywhere around me.")

    res = bridge.ground_pixel(*uv)
    if not res.get("ok"):
        reason = res.get("reason", "unknown")
        if reason == "no_reply_from_jetson":
            return ("I can see it, but the depth-grounding service on the "
                    "Jetson isn't answering, so I can't work out where it is "
                    "in the room.")
        return (f"I can see the {description}, but I couldn't measure its "
                f"distance (depth reading failed: {reason}) — it may be too "
                f"close, too far, or reflective.")

    goal = res["goal"]
    _mv._last_nav_requester = {
        "channel": state.get("channel") or "voice",
        "sender": state.get("sender_name") or "voice",
    }
    bridge.start_nav_to_pose(round(goal["x"], 2), round(goal["y"], 2),
                             round(math.degrees(goal["yaw"]), 1),
                             label=f"near the {description}")
    return (f"I can see the {description} — about {res['depth_m']:.1f} m away. "
            f"On my way; I'll say when I'm there.")


@tool
def scan_surroundings() -> str:
    """Turn a full slow circle in place so the depth camera can map everything
    around the robot (fills the 3D map behind/left/right). Use for "look
    around", "scan the room", "map this area", or before navigating in a spot
    the robot hasn't seen from all sides.

    Takes ~15 seconds. Returns a list of the objects seen during the scan."""
    bridge = _bridge.get()
    bridge.clear_motion_stop()
    # Head MUST be centred while the base turns: vSLAM/nvblox use the rigid
    # pan=0 extrinsic, and a panned head during motion corrupts the map.
    _mv.ensure_head_centred(bridge)

    try:
        from geometry_msgs.msg import Twist
    except ImportError:
        return "I can't turn right now — my wheel interface isn't available."
    twist = Twist()
    twist.angular.z = _mv._ANGULAR_VEL_RS
    step_dur = _mv._duration("L", _BASE_STEP_DEG)
    for _ in range(_BASE_STEPS):
        if not _mv._drive_for_duration(bridge, twist, step_dur):
            return "Scan stopped."
        # Pause so vSLAM/nvblox integrate a still frame (motion blur hurts both).
        end = time.time() + 1.0
        while time.time() < end:
            if bridge.motion_interrupted():
                return "Scan stopped."
            time.sleep(0.05)

    seen = bridge.get_detected_objects(max_age_s=30.0)
    if seen:
        labels = ", ".join(sorted(seen.keys()))
        return f"Scan complete — full circle mapped. I can see: {labels}."
    return ("Scan complete — full circle mapped. The detector didn't report "
            "any objects (it may not be running).")


@tool
def list_saved_locations() -> str:
    """List the named places the robot can navigate to with navigate_to_pose —
    both configured rooms and spots saved with save_location."""
    known = _bridge.get().get_known_locations()
    if not known:
        return "No locations saved yet. Stand me somewhere and say 'save this location as ...'."
    return "I can go to: " + ", ".join(sorted(known.keys())) + "."
