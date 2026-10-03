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
import re
import time
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import object_memory
from . import _bridge
from . import movement as _mv
from . import photo_recall as _recall
from . import photos as _photos
from . import survey as _survey

# How close the robot parks from the object (metres). Floor: D555 depth goes
# blind under ~0.4 m, and Nav2 can stop up to xy_goal_tolerance (0.10 m) short
# of the goal — don't set this below ~0.4 or the camera loses the target it
# just approached. The Jetson's pixel_to_goal.py reads the SAME env var, so
# exporting it once on both machines keeps the two halves agreeing.
_STANDOFF_M = float(os.environ.get("LANGROBO_STANDOFF_M", "0.45"))

# Search: rotate the base and re-check, one full circle. EIGHT views 45 deg
# apart, not four at 90: against the colour camera's ~87 deg, 90 deg steps
# left the seams between views at the very edge of both photos, where the VLM
# misses a half-cut object -- "go near the white chair" failed with the chair
# at a seam (owner, 2026-09-27). At 45 deg every direction is seen twice, once
# well inside the frame. A miss costs ~5 s of VLM per view (the reply is one
# short JSON), so the full circle is ~1-2 min; a hit stops early. Every view
# is logged as a photo (tools/photos.py), which ask_photos reads later. With
# LANGROBO_PHOTO_SURVEY=1 (off by default since 2026-10-02) it also goes to
# the background survey (tools/survey.py) and so into object memory.
_SEARCH_STEPS = 8
_SEARCH_STEP_DEG = 45.0


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
    'Look at this image. Find: "{description}". It counts even if it is only '
    "partly visible, cut off at the edge of the image, or seen from an unusual "
    "angle; colours may look different under indoor light. "
    "Reply with ONLY a JSON object, no other text: "
    '{{"found": true, "box": [ymin, xmin, ymax, xmax]}} or {{"found": false}}. '
    "The box is the tight bounding box of the whole object, in normalized "
    "image coordinates from 0 to 1000, where (0,0) is the top-left corner."
)


def _vlm_locate(frame: bytes, description: str) -> tuple | None:
    """Ask the multimodal LLM where `description` is in the JPEG frame.
    Returns (u, v, box) in colour-image pixels -- (u, v) the box centre, box
    (x0, y0, x1, y1) or None if the model gave only a point -- or None (not
    found / unparseable).

    A LOGGED photo (every _capture and look() is) is asked about through the
    photo log (photo_recall.locate_in): the same vision-tool slot keeps every
    photo cached, where a one-photo prompt here would throw them all out and
    the next ask_photos would re-read them (~20 s for 8). The one-photo prompt
    below is only for a photo the log does not hold (no camera stamp)."""
    try:
        return _recall.locate_in(frame, description)
    except LookupError:
        pass
    import base64
    import io
    import json as _json
    import re

    from langchain_core.messages import HumanMessage
    from PIL import Image

    from ..utils.speech_stream import strip_thought_residue

    width, height = Image.open(io.BytesIO(frame)).size
    b64 = base64.b64encode(frame).decode()
    # Unstreamed: streamed, this llama.cpp files an answer wrapped in Gemma's
    # channel markers as hidden reasoning, the text arrives empty, and an
    # empty reply here reads as "not found" -- a silent miss (2026-09-27).
    # The vision-TOOL slot, not local_agent's: a one-shot photo prompt there
    # would overwrite the vision conversation's cache (survey.VISION_TOOL_SLOT).
    reply = _survey.vision_tool_llm().invoke([HumanMessage(content=[
        {"type": "text", "text": _VLM_LOCATE_PROMPT.format(description=description)},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ])], config={"run_name": "vlm_locate", "tags": ["vlm_locate"]})
    m = re.search(r"\{.*\}", strip_thought_residue(str(reply.content)), re.DOTALL)
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


def _capture(bridge, settle_s: float = 2.5, source: str = "search") -> tuple:
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
    pose, when, epoch = bridge.get_current_pose(), time.time(), bridge.get_origin_epoch()
    _photos.record(frame, stamp, pose, when, epoch, source)
    # Opt-in (LANGROBO_PHOTO_SURVEY=1): the survey also puts everything in it
    # into object memory. Off, submit() returns at once.
    _survey.submit(frame, stamp, pose, source, when=when, epoch=epoch)
    return frame, {"stamp": stamp, "pose": pose, "when": when}


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


# The depth stream stopped around the photo. Seen 2026-09-26: the camera sent
# no depth for up to 9 s while the Jetson's CPU sat at 76-88% on every core,
# and a bottle the VLM had just found could not be measured. One fresh photo
# a moment later usually lands in a good stretch.
_DEPTH_STALL = ("no_depth_frame", "no_depth_near_stamp", "snapshot_expired")
_STALL_RETRY_S = 1.5


def _ground_retrying(bridge, description: str, uv: tuple, capture: dict | None) -> tuple:
    """_ground, and once more on a fresh photo if the depth stream had
    stalled. Returns (result, capture of the photo the result came from)."""
    res = _ground(bridge, uv, capture)
    if res.get("ok") or res.get("reason") not in _DEPTH_STALL:
        return res, capture
    time.sleep(_STALL_RETRY_S)
    frame, fresh = _capture(bridge)
    if frame is None:
        return res, capture
    try:
        uv2 = _vlm_locate(frame, description)
    except Exception:
        return res, capture
    if uv2 is None:
        return res, capture
    return _ground(bridge, uv2, fresh), fresh



# A remembered object this close to straight ahead is already in the middle of
# the colour camera's ~87 deg view: look without turning.
_FACE_WITHIN_DEG = 20.0
# Memory step (see approach step 1). Judge from here only when close: at 2.5 m
# a bottle is a few dozen pixels and easily hidden. The viewpoint is 1 m in
# front of where it was -- far enough that the camera (blind under ~0.4 m,
# ~87 deg wide) sees it and its surroundings, close enough to see it well.
_JUDGE_FROM_HERE_M = 2.5
_VIEW_FROM_M = 1.0
_ALREADY_THERE_M = 0.3    # within this of the viewpoint: look without driving




def _say(bridge, state: dict | None, text: str) -> bool:
    """A short progress line while a long search or drive runs, so the robot
    is not silent for a minute (owner, 2026-10-02). Voice turns only:
    Telegram already shows "typing...", and a reply there is one message.

    Not a speak() tool (CLAUDE.md rule 4): code says it, at fixed moments, in
    a COMPLETE utterance -- tts_node mutes the mic while an utterance is open,
    and one left open across a minute-long search would make the robot deaf
    to new commands. Never fails the search."""
    if (state or {}).get("channel") != "voice":
        return False
    try:
        bridge.publish_speech(text)
        return True
    except Exception:
        return False


def _face(bridge, bearing_deg: float) -> tuple:
    """Turn to a bearing (0 = ahead) unless it is already in the middle of the
    view. (ok, why) as movement.turn_robot."""
    if abs(bearing_deg) <= _FACE_WITHIN_DEG:
        return True, ""
    return _mv.turn_robot(bridge, bearing_deg)


@tool
def approach_described_object(description: str,
                              state: Annotated[dict, InjectedState],
                              then: str = "") -> str:
    """Find a described object with the camera and drive up close to it,
    avoiding obstacles (Nav2). Use for ANY object the user describes but has
    not saved as a location: "the red coffee mug", "my black backpack", "the
    chair", "the surf excel packet".

    If the robot has seen it before (in any photo, from anywhere), it goes to
    where it was -- turning to it if it is close, driving over if it is far or
    out of view -- checks it is still there, and if not, searches around that
    spot. Otherwise it turns in exact 45 degree steps and re-checks, up to a
    full circle (each check takes a few seconds — the vision model looks at a
    fresh photo every step). This can take a minute or two; call it once.

    Returns once the object is found and the drive starts — the drive
    continues in the background and a system message reports arrival.

    then: what to do on ARRIVAL, if the user asked for more than the drive
    ("go to the box and tell me what is on it" -> then="tell the user what is
    on the box"). It is done automatically when the robot gets there; do not
    do it now. Leave empty for a plain drive."""
    # No photo survey while searching: the Mac runs one model, and every view
    # of the search waits on it. The views are queued and surveyed after.
    with _survey.paused():
        return _approach(description, state, then)


def _approach(description: str, state: dict, then: str = "") -> str:
    bridge = _bridge.get()
    description = description.strip()
    # for the replies: "the orange bottle" -> "orange bottle", so the text does
    # not read "the the orange bottle" (floor test, 2026-09-26)
    name = re.sub(r"^(the|a|an)\s+", "", description, flags=re.IGNORECASE)
    bridge.clear_motion_stop()

    try:
        from geometry_msgs.msg import Twist
    except ImportError:
        Twist = None                    # Studio/tests — forward check only

    # Checked before the search, not after: locating the object costs a VLM
    # round trip per step (10-40 s each) and the search ROTATES the base. Both
    # are wasted if the wheels are being zeroed by teleop anyway.
    refusal = _mv.blocked_by_role(state) or _mv.blocked_by_manual()
    if refusal:
        return refusal

    uv, capture = None, None
    note = ""            # what the photo check found, said before the rest
    first_view = 0       # 1 = the view ahead has been checked already
    at_its_spot = False  # looking from where it was seen (so "moved?" means something)

    # ── 1. The photos first: seen before -> go to where it was, THEN look ───
    # Every photo the robot took this session is in the photo log; one
    # question over all of them says which photo shows it and where in it,
    # and the Jetson places it with THAT photo's depth and pose
    # (photo_recall.ask) -- so from wherever the robot is now, its distance
    # and direction are known. Things move, so it is a place to check, never
    # a place to drive at blind:
    #   a) close enough to judge from here: face it and look. There -> go.
    #   b) not seen from here -- far away, hidden, an old photo: DRIVE to a
    #      viewpoint in front of where it was and look there; still not
    #      there -> search AROUND THAT SPOT (step 2 runs there).
    last_seen = None     # the photo-log answer
    try:
        last_seen = _recall.ask(f"where is {description}?")
    except Exception:
        last_seen = None # the vision model failed: the search still works
    if not (last_seen and last_seen.get("ok") and last_seen.get("photo") is not None):
        last_seen = None
    placed = (last_seen or {}).get("placed")
    if last_seen is not None:
        age = _recall.describe_age(last_seen["age_s"])
        dist, bearing = last_seen.get("distance_m"), last_seen.get("bearing_deg")
        looked_here = False

        # a) judge from here (a direction-only answer is judged from here too)
        if bearing is not None and (dist is None or dist <= _JUDGE_FROM_HERE_M) \
                and (abs(bearing) <= _FACE_WITHIN_DEG or Twist is not None):
            ok, why = _face(bridge, bearing)
            if why == "interrupted":
                return f"Stopped looking for the {name}."
            if ok:
                frame, capture = _capture(bridge)
                if frame is None:
                    if bridge.motion_interrupted():
                        return f"Stopped looking for the {name}."
                    return ("My camera feed isn't giving me a fresh image right now, "
                            "so I can't look for it.")
                try:
                    uv = _vlm_locate(frame, description)
                except Exception as e:
                    return (f"I couldn't analyse the camera image (vision model error: "
                            f"{type(e).__name__}). Try again in a moment.")
                looked_here = True
                at_its_spot = uv is not None

        # b) not seen from here: go to where it was, and look there
        if uv is None and placed and Twist is not None:
            pose = bridge.get_current_pose()
            went = True
            drove = dist is not None and dist > _VIEW_FROM_M + _ALREADY_THERE_M
            if drove and pose:
                vx, vy, vyaw = compute_standoff_goal(pose[0], pose[1], placed["x"], placed["y"],
                                                     _VIEW_FROM_M)
                _say(bridge, state, f"I saw the {name} over there {age}. Going to check.")
                leg = bridge.reach_and_wait(round(vx, 2), round(vy, 2), round(vyaw, 1))
                if leg.get("result") == "interrupted":
                    return f"Stopped going to look for the {name}."
                if not leg.get("ok"):
                    went = False
                    note = (f"I saw the {name} {age} over there, but couldn't get to that "
                            f"spot ({leg.get('why') or leg.get('result')}), so I'm "
                            f"searching from here. ")
            at_its_spot = went
            if went and not drove and looked_here:
                first_view = 1          # at the viewpoint already, and just looked
                note = (f"The {name} wasn't right where I saw it {age}, so I'm "
                        f"looking around that spot. ")
            elif went:
                rel2 = object_memory.relative(placed, bridge.get_current_pose())
                ok, why = _face(bridge, rel2[1] if rel2 else 0.0)
                if why == "interrupted":
                    return f"Stopped looking for the {name}."
                frame, capture = _capture(bridge)
                if frame is None:
                    if bridge.motion_interrupted():
                        return f"Stopped looking for the {name}."
                    return ("My camera feed isn't giving me a fresh image right now, "
                            "so I can't look for it.")
                try:
                    uv = _vlm_locate(frame, description)
                except Exception as e:
                    return (f"I couldn't analyse the camera image (vision model error: "
                            f"{type(e).__name__}). Try again in a moment.")
                if uv is None:
                    first_view = 1
                    note = (f"The {name} wasn't right where I saw it {age}, so I'm "
                            f"looking around that spot. ")
        elif uv is None and looked_here:
            first_view = 1
            at_its_spot = True
            note = (f"The {name} wasn't where I saw it {age}, so I'm looking "
                    f"around. ")

    # ── 2. Search: the view ahead, then exact 45 degree turns ────────────────
    # Views are kept as headings relative to where the search started. Left
    # first; when a turn is refused (something in the swing -- 2026-09-27, a
    # box 0.33 m away ended the search after 2 views), the rest of the circle
    # is covered from the other side, and only both ways blocked gives up.
    # Each turn is aimed from the MEASURED heading, not the planned one: a
    # refused turn may already have swung part-way (goal_exec stops where the
    # obstacle is), and turning on from the plan would skew every later view.
    seen = {0} if first_view else set()
    offset, direction = 0, 1
    start = bridge.get_current_pose()
    while uv is None and len(seen) < _SEARCH_STEPS:
        if bridge.motion_interrupted():
            return f"Stopped searching for the {name}."
        if 0 in seen:
            if Twist is None:
                break
            k = next(k for k in range(1, _SEARCH_STEPS + 1)
                     if (offset + direction * k * _SEARCH_STEP_DEG) % 360 not in seen)
            now = bridge.get_current_pose()
            drift = 0.0
            if start and now:
                drift = (offset - (now[2] - start[2]) + 180.0) % 360.0 - 180.0
            if len(seen) == 1:
                if _say(bridge, state, note.strip() or
                        f"I don't see the {name} from here, so I'm looking around."):
                    note = ""                  # said already: not again in the answer
            elif len(seen) == _SEARCH_STEPS // 2:
                _say(bridge, state, f"Still looking for the {name}.")
            ok, why = _mv.turn_robot(bridge, direction * k * _SEARCH_STEP_DEG + drift)
            if not ok:
                if why == "interrupted":
                    return f"Stopped searching for the {name}."
                if direction == 1:
                    direction = -1            # blocked this way: finish the circle the other way
                    continue
                return (note + f"I couldn't turn either way to keep looking for the "
                        f"{name} ({why}). It isn't in the {len(seen)} view(s) I checked.")
            offset = int(offset + direction * k * _SEARCH_STEP_DEG) % 360
        frame, capture = _capture(bridge)
        if frame is None:
            if bridge.motion_interrupted():
                return f"Stopped searching for the {name}."
            return ("My camera feed isn't giving me a fresh image right now, "
                    "so I can't look for it.")
        try:
            uv = _vlm_locate(frame, description)
        except Exception as e:
            return (f"I couldn't analyse the camera image (vision model error: "
                    f"{type(e).__name__}). Try again in a moment.")
        seen.add(offset)

    if uv is None:
        if last_seen is not None:
            return (note + f"I saw the {name} {_recall.describe_age(last_seen['age_s'])} "
                    f"(photo {last_seen['photo']}), but it isn't there now and I couldn't "
                    f"find it anywhere around me — someone may have moved it.")
        return (note + f"I turned a full circle and looked carefully, but I couldn't "
                f"spot the {name} anywhere around me.")

    res, capture = _ground_retrying(bridge, description, uv, capture)
    if not res.get("ok"):
        reason = res.get("reason", "unknown")
        if reason == "no_reply_from_jetson":
            return ("I can see it, but the depth-grounding service on the "
                    "Jetson isn't answering, so I can't work out where it is "
                    "in the room.")
        return (f"I can see the {name}, but I couldn't measure its "
                f"distance (depth reading failed: {reason}) — it may be too "
                f"close, too far, or reflective.")

    obj = res.get("object")
    if placed and obj and at_its_spot:
        moved = math.hypot(obj["x"] - placed["x"], obj["y"] - placed["y"])
        note = ("It's still where I saw it. " if moved <= object_memory.SAME_OBJECT_M
                else f"It has moved about {moved:.1f} m since I last saw it. ")

    goal = res["goal"]
    # Nav completion arrives minutes later as a [SYSTEM] turn — remember who
    # asked so a Telegram-initiated approach reports back to that chat.
    _mv.remember_requester(state, then, target=description)
    bridge.start_nav_to_pose(round(goal["x"], 2), round(goal["y"], 2),
                             round(math.degrees(goal["yaw"]), 1),
                             label=f"near the {name}")
    return (note + f"I can see the {name} — about {res['depth_m']:.1f} m away. "
            f"On my way; I'll say when I'm there." + _mv.view_stale_note())


def arrival_check(description: str) -> str:
    """Is the object really in front of the robot now that it has arrived?
    One fresh photo, asked the same "is it clearly visible?" question as the
    search, measured with depth when it is there. Returns a sentence for the
    arrival report, or "" if the check itself could not run -- a broken
    camera or model must never hold up the report.

    Why: arrival meant "reach got to the goal", not "the thing is here". A
    goal placed from a wrong pixel, or a thing moved while driving, still
    arrived -- and the robot said so (FIND_AND_GO.md, Known limits)."""
    name = re.sub(r"^(the|a|an)\s+", "", (description or "").strip(), flags=re.IGNORECASE)
    if not name:
        return ""
    bridge = _bridge.get()
    try:
        frame, capture = _capture(bridge, source="arrival")
        if frame is None:
            return ""
        uv = _vlm_locate(frame, description)
        if uv is None:
            return (f" But I can't see the {name} in front of me now -- it may have "
                    f"moved, or I may be facing away from it.")
        res = _ground(bridge, uv, capture)
        rel = res.get("relative") if res.get("ok") else None
        if rel:
            dist = math.hypot(rel["forward_m"], rel["left_m"])
            return f" I can see the {name} in front of me, about {dist:.1f} m away."
        return f" I can see the {name} in front of me."
    except Exception:
        return ""


@tool
def scan_surroundings(state: Annotated[dict | None, InjectedState] = None) -> str:
    """Turn a full slow circle in place so the depth camera can map everything
    around the robot (fills the 3D map behind/left/right). Use for "look
    around", "scan the room", "map this area", or before navigating in a spot
    the robot hasn't seen from all sides.

    Takes about 15 seconds."""
    refusal = _mv.blocked_by_role(state) or _mv.blocked_by_manual()   # MANUAL: a turn against its zeros goes nowhere
    if refusal:
        return refusal
    bridge = _bridge.get()
    bridge.clear_motion_stop()

    try:
        from geometry_msgs.msg import Twist  # noqa: F401 -- the timed fallback needs it
    except ImportError:
        return "I can't turn right now — my wheel interface isn't available."
    photos = 0
    for i in range(6):
        ok, why = _mv.turn_robot(bridge, 60.0)
        if not ok:
            if why == "interrupted":
                return "Scan stopped."
            # Said so it cannot be read as success: "Scan stopped after 0
            # degrees" got "I have scanned the area around me" back from the
            # model 3 times in 5 (a Telegram "Now check", 2026-10-02); this
            # wording, 5 of 5 honest.
            if i == 0:
                return (f"I could NOT look around: I could not turn at all ({why}). "
                        f"Nothing was scanned. Tell the user that, and why.")
            return (f"I only looked around {i * 60} of 360 degrees, then could not "
                    f"turn further ({why}). Tell the user the scan is incomplete."
                    + _mv.view_stale_note())
        # Pause so vSLAM/nvblox integrate a still frame (motion blur hurts both).
        end = time.time() + 1.0
        while time.time() < end:
            if bridge.motion_interrupted():
                return "Scan stopped."
            time.sleep(0.05)
        # And keep the view: every photo goes into the photo log, so "where
        # is my bag?" after "look around" is answered from these six.
        frame, _ = _capture(bridge, settle_s=1.0, source="scan")
        photos += frame is not None
    return (f"Scan complete — I turned a full circle, so the map now covers "
            f"all around me, and I took {photos} photo(s) to remember what is "
            f"where." + _mv.view_stale_note())


@tool
def list_saved_locations() -> str:
    """List the named places the robot can navigate to with navigate_to_pose —
    both configured rooms and spots saved with save_location."""
    known = _bridge.get().get_known_locations()
    if not known:
        return "No locations saved yet. Stand me somewhere and say 'save this location as ...'."
    return "I can go to: " + ", ".join(sorted(known.keys())) + "."
