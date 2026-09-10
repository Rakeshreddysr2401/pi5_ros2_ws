"""approach_described_object() — "go to the red bottle".

THE object-approach path on this rover, and the only one that works. The VLM
looks at the camera frame and points at the object; the Jetson deprojects that
pixel with real depth into a Nav2 goal that already has the standoff applied;
Nav2 drives there avoiding obstacles.

    look frame ──▶ VLM: "where is the red bottle?" ──▶ pixel (u, v)
                                                          │
    Jetson pixel_to_goal: depth + camera intrinsics + TF ─┘
                                                          ▼
                                          Nav2 goal in odom ──▶ drive

There used to be a second, faster path (approach_object) built on YOLO
detections streaming from the Jetson on /vision/detections_3d, backed by a
persistent world model of where things are. Nothing on this rover has ever
published that topic, so every call answered "I have nowhere to go" about an
object in plain view. It was removed rather than left looking functional —
see INTEGRATION_GAPS.md §1 for the exact JSON contract to restore it against.

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

# How close the robot parks from the object (metres). Floor: D555 depth goes
# blind under ~0.4 m, and Nav2 can stop up to xy_goal_tolerance (0.10 m) short
# of the goal — don't set this below ~0.4 or the camera loses the target it
# just approached. The Jetson's pixel_to_goal.py reads the SAME env var, so
# exporting it once on both machines keeps the two halves agreeing.
_STANDOFF_M = float(os.environ.get("LANGROBO_STANDOFF_M", "0.45"))

# Search: rotate the base and re-check. Each check is a VLM round-trip
# (~10-40 s), so the sweep is bounded at a full circle plus one recheck of the
# starting orientation (odometry under-rotates).
_SEARCH_STEPS = 5
_SEARCH_STEP_DEG = 90.0


def compute_standoff_goal(rx: float, ry: float, ox: float, oy: float,
                          standoff: float) -> tuple:
    """Nav2 goal (gx, gy, yaw_deg) that parks `standoff` metres short of the
    object (ox, oy) on the robot→object line, facing the object.

    If the robot is already inside the standoff ring, the goal is the robot's
    own position with the yaw turned to face the object (Nav2 rotates in
    place). Mirrors the Jetson's pixel_to_goal.compute_standoff_goal, which
    returns radians; this one returns degrees, which is what start_nav_to_pose
    takes."""
    dx, dy = ox - rx, oy - ry
    dist = math.hypot(dx, dy)
    yaw_deg = math.degrees(math.atan2(dy, dx))
    if dist <= standoff or dist < 1e-6:
        return (rx, ry, yaw_deg)
    scale = (dist - standoff) / dist
    return (rx + dx * scale, ry + dy * scale, yaw_deg)


# ── VLM pixel grounding ─────────────────────────────────────────────────────

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
    """Frame captured AFTER now — the look feed needs a beat to publish a
    post-motion view; a pre-rotation cache hit would re-check the old view."""
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
    """Find a described object with the camera and drive up close to it,
    avoiding obstacles (Nav2). Use for ANY object the user describes but has
    not saved as a location: "the red coffee mug", "my black backpack", "the
    chair", "the surf excel packet".

    If the object isn't in the current view the robot turns in 90 degree steps
    and re-checks, up to a full circle (each check takes a while — the vision
    model looks at a fresh photo every step).

    Returns once the object is found and the drive starts — the drive
    continues in the background and a system message reports arrival."""
    bridge = _bridge.get()
    description = description.strip()
    bridge.clear_motion_stop()

    try:
        from geometry_msgs.msg import Twist
    except ImportError:
        Twist = None                    # Studio/tests — forward check only

    # Checked before the search, not after: locating the object costs a VLM
    # round trip per step (10-40 s each) and the search ROTATES the base. Both
    # are wasted if the wheels are being zeroed by teleop anyway.
    refusal = _mv.blocked_by_manual()
    if refusal:
        return refusal

    uv = None
    for step in range(_SEARCH_STEPS):
        if bridge.motion_interrupted():
            return f"Stopped searching for the {description}."
        if step > 0:
            if Twist is None:
                break
            twist = Twist()
            twist.angular.z = _mv._ANGULAR_VEL_RS
            if not _mv._drive_for_duration(
                    bridge, twist, _mv._duration("L", _SEARCH_STEP_DEG)):
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
    # Nav completion arrives minutes later as a [SYSTEM] turn — remember who
    # asked so a Telegram-initiated approach reports back to that chat.
    _mv._last_nav_requester = {
        "channel": state.get("channel") or "voice",
        "sender": state.get("sender_name") or "voice",
    }
    bridge.start_nav_to_pose(round(goal["x"], 2), round(goal["y"], 2),
                             round(math.degrees(goal["yaw"]), 1),
                             label=f"near the {description}")
    return (f"I can see the {description} — about {res['depth_m']:.1f} m away. "
            f"On my way; I'll say when I'm there." + _mv._VIEW_STALE_NOTE)


@tool
def scan_surroundings() -> str:
    """Turn a full slow circle in place so the depth camera can map everything
    around the robot (fills the 3D map behind/left/right). Use for "look
    around", "scan the room", "map this area", or before navigating in a spot
    the robot hasn't seen from all sides.

    Takes about 15 seconds."""
    bridge = _bridge.get()
    bridge.clear_motion_stop()

    try:
        from geometry_msgs.msg import Twist
    except ImportError:
        return "I can't turn right now — my wheel interface isn't available."
    twist = Twist()
    twist.angular.z = _mv._ANGULAR_VEL_RS
    step_dur = _mv._duration("L", 60.0)
    for _ in range(6):
        if not _mv._drive_for_duration(bridge, twist, step_dur):
            return "Scan stopped."
        # Pause so vSLAM/nvblox integrate a still frame (motion blur hurts both).
        end = time.time() + 1.0
        while time.time() < end:
            if bridge.motion_interrupted():
                return "Scan stopped."
            time.sleep(0.05)
    return ("Scan complete — I turned a full circle, so the map now covers "
            "all around me." + _mv._VIEW_STALE_NOTE)


@tool
def list_saved_locations() -> str:
    """List the named places the robot can navigate to with navigate_to_pose —
    both configured rooms and spots saved with save_location."""
    known = _bridge.get().get_known_locations()
    if not known:
        return "No locations saved yet. Stand me somewhere and say 'save this location as ...'."
    return "I can go to: " + ", ".join(sorted(known.keys())) + "."
