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
# (~10-40 s), so the sweep is bounded at one full circle: four views, 90 deg
# apart, against the colour camera's ~87 deg. There used to be a fifth, a
# recheck of the start, because the TIMED turns under-rotated; the turns are
# now closed on the measured heading (movement.turn_robot -> goal_exec), so
# the fourth turn lands where the first view was. Overlapping views and one
# VLM call that lists everything are the next step (INTELLIGENCE_PLAN.md B5).
_SEARCH_STEPS = 4
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

# A BOX, not a point (floor test 2026-09-26): Gemma's x is good to ~5 px but
# its y is off by up to ~45 px either way, so a single "centre" pixel on a
# thin object often lands on what is behind it -- a bottle 1 m away was
# grounded on the door 2 m away. The box goes to the Jetson, which takes the
# nearest solid slab above the floor inside it (pixel_to_goal.py THE
# OBJECT'S BOX, NOT ONE PIXEL). A reply with only x, y is still accepted.
_VLM_LOCATE_PROMPT = (
    'Look at this image. Find: "{description}". '
    "Reply with ONLY a JSON object, no other text: "
    '{{"found": true, "box": [ymin, xmin, ymax, xmax]}} or {{"found": false}}. '
    "The box is the tight bounding box of the whole object, in normalized "
    "image coordinates from 0 to 1000, where (0,0) is the top-left corner."
)


def _vlm_locate(frame: bytes, description: str) -> tuple | None:
    """Ask the multimodal LLM where `description` is in the JPEG frame.
    Returns (u, v, box) in colour-image pixels -- (u, v) the box centre, box
    (x0, y0, x1, y1) or None if the model gave only a point -- or None (not
    found / unparseable)."""
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
    sx, sy = width / 1000.0, height / 1000.0
    try:
        ymin, xmin, ymax, xmax = (float(a) for a in data["box"])
        if all(0 <= a <= 1000 for a in (ymin, xmin, ymax, xmax)) and xmin < xmax and ymin < ymax:
            box = (xmin * sx, ymin * sy, xmax * sx, ymax * sy)
            return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box)
    except (KeyError, TypeError, ValueError):
        pass
    try:
        x, y = float(data["x"]), float(data["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        return None
    return (x * sx, y * sy, None)


def _capture(bridge, settle_s: float = 2.5) -> tuple:
    """(jpeg, capture) for a frame taken NOW, or (None, None).

    capture = {"stamp": the photo's camera stamp or None, "pose": where the
    robot was when it was taken}. The Jetson is told to hold that photo's
    depth and camera pose at once (bridge.hold_frame), because the VLM will
    take 10-40 s to pick a pixel and by then its buffers have moved on --
    grounding against the NEWEST depth and pose instead is how an object seen
    at one heading got placed at another (rover repo INTELLIGENCE_PLAN.md B2).

    Frame captured AFTER now -- the look feed needs a beat to publish a
    post-motion view; a pre-rotation cache hit would re-check the old view."""
    end = time.time() + settle_s
    frame, stamp = None, None
    while time.time() < end:
        if bridge.motion_interrupted():
            return None, None
        frame, stamp = bridge.get_frame_stamped(max_age_s=0.8)
        if frame is not None:
            break
        time.sleep(0.15)
    if frame is None:
        frame, stamp = bridge.get_frame_stamped(max_age_s=10.0)   # degraded: newest we have
        if frame is None:
            return None, None
    if stamp:
        bridge.hold_frame(stamp)
    return frame, {"stamp": stamp, "pose": bridge.get_current_pose()}


# The photo's own depth and pose are gone (the hold did not arrive, or the
# depth stream had a gap there). The newest depth and pose describe the same
# view only if the robot has not moved since the photo.
_REGROUND_REASONS = ("snapshot_expired", "no_depth_near_stamp")
_STILL_M, _STILL_DEG = 0.02, 1.0


def _ground(bridge, uv: tuple, capture: dict | None) -> dict:
    """ground_pixel at the moment of the photo, when the photo has a stamp,
    on the VLM's box when it gave one. uv = (u, v) or (u, v, box)."""
    u, v, *rest = uv
    box = rest[0] if rest else None
    stamp = (capture or {}).get("stamp")
    res = bridge.ground_pixel(u, v, stamp=stamp, box=box)
    if stamp and not res.get("ok") and res.get("reason") in _REGROUND_REASONS:
        then, now = capture.get("pose"), bridge.get_current_pose()
        if then and now and math.hypot(now[0] - then[0], now[1] - then[1]) <= _STILL_M \
                and abs((now[2] - then[2] + 180.0) % 360.0 - 180.0) <= _STILL_DEG:
            res = bridge.ground_pixel(u, v, box=box)
    return res


@tool
def approach_described_object(description: str,
                              state: Annotated[dict, InjectedState]) -> str:
    """Find a described object with the camera and drive up close to it,
    avoiding obstacles (Nav2). Use for ANY object the user describes but has
    not saved as a location: "the red coffee mug", "my black backpack", "the
    chair", "the surf excel packet".

    If the object isn't in the current view the robot turns in exact 90 degree
    steps and re-checks, up to a full circle (each check takes a while — the
    vision model looks at a fresh photo every step).

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
    capture = None
    for step in range(_SEARCH_STEPS):
        if bridge.motion_interrupted():
            return f"Stopped searching for the {description}."
        if step > 0:
            if Twist is None:
                break
            ok, why = _mv.turn_robot(bridge, _SEARCH_STEP_DEG)
            if not ok:
                if why == "interrupted":
                    return f"Stopped searching for the {description}."
                return (f"I couldn't turn to keep looking for the {description} "
                        f"({why}). It isn't in the {step} view(s) I checked.")
        frame, capture = _capture(bridge)
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

    res = _ground(bridge, uv, capture)
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
            f"On my way; I'll say when I'm there." + _mv.view_stale_note())


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
        from geometry_msgs.msg import Twist  # noqa: F401 -- the timed fallback needs it
    except ImportError:
        return "I can't turn right now — my wheel interface isn't available."
    for i in range(6):
        ok, why = _mv.turn_robot(bridge, 60.0)
        if not ok:
            if why == "interrupted":
                return "Scan stopped."
            return (f"Scan stopped after {i * 60} degrees: I couldn't turn further "
                    f"({why})." + (_mv.view_stale_note() if i else ""))
        # Pause so vSLAM/nvblox integrate a still frame (motion blur hurts both).
        end = time.time() + 1.0
        while time.time() < end:
            if bridge.motion_interrupted():
                return "Scan stopped."
            time.sleep(0.05)
    return ("Scan complete — I turned a full circle, so the map now covers "
            "all around me." + _mv.view_stale_note())


@tool
def list_saved_locations() -> str:
    """List the named places the robot can navigate to with navigate_to_pose —
    both configured rooms and spots saved with save_location."""
    known = _bridge.get().get_known_locations()
    if not known:
        return "No locations saved yet. Stand me somewhere and say 'save this location as ...'."
    return "I can go to: " + ", ".join(sorted(known.keys())) + "."
