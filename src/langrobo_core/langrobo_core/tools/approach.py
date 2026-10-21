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

import logging
import math
import os
import re
import threading
import time
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import notices as _notices
from ..services import object_memory
from . import _bridge
from . import movement as _mv
from . import photo_recall as _recall
from . import photos as _photos
from . import survey as _survey
from ..utils import viewpoints as _viewpoints

logger = logging.getLogger(__name__)

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
# Spots searched in all (2026-10-13): after a circle finds nothing, drive to an
# open, not-yet-searched spot and circle again (utils/viewpoints.py).
_SEARCH_SPOTS = int(os.environ.get("LANGROBO_SEARCH_SPOTS", "3"))


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


# Depth failures the Jetson can report, in language a user can act on. Every
# one of these means "the pixel was found but the sensor could not measure it",
# which is a different answer from "I cannot see it" and must not be collapsed
# into one — the object IS there.
DEPTH_FAIL_HELP = {
    "no_depth_at_pixel": ("the depth sensor has no reading there — it may be "
                          "glass, a mirror, something very dark, or an edge"),
    "depth_out_of_range": ("it is outside the depth camera's usable range — "
                           "closer than 0.3 m or too far away"),
    "no_depth_frame": "the depth stream has stopped",
    "no_camera_info": "the camera calibration is not being published",
    "pixel_out_of_bounds": "I picked a point outside the image",
    "no_robot_pose": "I do not currently know where I am",
    "no_reply_from_jetson": ("the depth service on the Jetson is not "
                             "answering"),
    # Grounding at the moment of the photo (approach._ground) failed and the
    # robot has moved since, so the newest depth is a different view.
    "snapshot_expired": ("the depth for that photo is no longer held and I "
                         "have moved since, so I need a fresh look"),
    "no_depth_near_stamp": ("the depth stream had a gap when that photo was "
                            "taken and I have moved since"),
    "tf_failed": "I could not tell where the camera was when the photo was taken",
    # _ground_retrying: the first photo's depth failed, and the fresh photo
    # taken to measure again no longer shows it -- a person who walked off.
    "lost_sight": "when I looked again to measure it, it was no longer in view -- it may have moved",
}


# The photo's own depth and pose are gone (the hold did not arrive, or the
# depth stream had a gap there). The newest depth and pose describe the same
# view only if the robot has not moved since the photo.
_REGROUND_REASONS = ("snapshot_expired", "no_depth_near_stamp")
_STILL_M, _STILL_DEG = 0.02, 1.0


def _ground(bridge, uv: tuple, capture: dict | None, what: str | None = None) -> dict:
    """ground_pixel at the moment of the photo, when the photo has a stamp,
    on the VLM's box when it gave one. uv = (u, v) or (u, v, box). what: the
    description, so the Jetson can measure doors/walls with the LiDAR."""
    u, v, *rest = uv
    box = rest[0] if rest else None
    stamp = (capture or {}).get("stamp")
    res = bridge.ground_pixel(u, v, stamp=stamp, box=box, what=what)
    if stamp and not res.get("ok") and res.get("reason") in _REGROUND_REASONS:
        then, now = capture.get("pose"), bridge.get_current_pose()
        if then and now and math.hypot(now[0] - then[0], now[1] - then[1]) <= _STILL_M \
                and abs((now[2] - then[2] + 180.0) % 360.0 - 180.0) <= _STILL_DEG:
            res = bridge.ground_pixel(u, v, box=box, what=what)
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
    res = _ground(bridge, uv, capture, description)
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
        return {"ok": False, "reason": "lost_sight", "first": res.get("reason")}, capture
    return _ground(bridge, uv2, fresh, description), fresh



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


# Who a person IS, as opposed to what they look like. "Go to my dad" on
# 2026-10-04 drove at the first man the camera saw -- the owner's brother, in
# another room: the vision model was asked for "the man who is my Dad" and had
# nothing to go on but "man". Not intent matching (CLAUDE.md): the model has
# already chosen to approach; this checks whether the description gives the
# camera anything to find. Telugu kinship words too -- the household speaks it.
_RELATIONS = {
    "dad", "daddy", "father", "papa", "mom", "mum", "mummy", "mommy", "mother",
    "brother", "bro", "sister", "sis", "son", "daughter", "wife", "husband",
    "grandma", "grandmother", "granny", "grandpa", "grandfather", "uncle",
    "aunt", "aunty", "auntie", "cousin", "friend", "boss", "nephew", "niece",
    "nanna", "amma", "anna", "akka", "tammudu", "thammudu", "chelli", "babai",
    "pinni", "mavayya", "atha", "tatayya", "ammamma", "nanamma",
}
_PERSON_WORDS = {"man", "woman", "person", "guy", "lady", "boy", "girl", "kid",
                 "child", "someone", "he", "she", "him", "her", "people"}
_FILLER = {"the", "a", "an", "my", "our", "your", "his", "their", "who", "is",
           "that", "which", "named", "called", "of", "me", "mine", "elder",
           "younger", "little", "big", "older"}


def _names_a_person_only(description: str) -> bool:
    """True when the description names a person -- a relation ("my dad") or a
    household member's name -- and says nothing the camera could see."""
    words = re.findall(r"[a-z]+", description.casefold())
    if not words:
        return False
    names = set()
    try:
        from ..services import telegram as telegram_service
        svc = telegram_service.get()
        if svc is not None:
            names = {n.casefold() for n in svc.member_names()}
    except Exception:
        pass
    who = _RELATIONS | names
    if not any(w in who for w in words):
        return False
    return all(w in who or w in _PERSON_WORDS or w in _FILLER for w in words)


@tool
def approach_described_object(description: str,
                              state: Annotated[dict, InjectedState],
                              then: str = "") -> str:
    """Find a described object with the camera and drive up close to it,
    avoiding obstacles (Nav2). Use for ANY object the user describes but has
    not saved as a location: "the red coffee mug", "my black backpack", "the
    chair", "the surf excel packet". A person is described by how they look
    ("the man in the blue shirt") -- it cannot tell who someone is.

    It uses the photos already taken first, otherwise searches by turning in
    place. Call it once: it returns within seconds, the search and the drive go
    on in the background, and the result and the arrival are reported to the
    user automatically.

    then: what to do on ARRIVAL, in plain words, if the user asked for more than the drive
    ("go to the box and tell me what is on it" -> then="tell the user what is
    on the box"). It is done automatically when the robot gets there; do not
    do it now. Leave empty for a plain drive.

    Asked again for the SAME object while already searching for or driving to
    it ("...and send me a pic when you get there"), it does not restart: it
    adds `then` to the drive under way."""
    description = description.strip()
    name = _short_name(description)
    bridge = _bridge.get()
    already = _already_after(bridge, name)
    if already:
        if already == "searching":
            with _job_lock:
                _job["then"] = _join_errands(_job["then"], then)
        else:
            _mv.add_errand(then)
        doing = "looking for" if already == "searching" else "on my way to"
        return (f"I'm already {doing} the {name}"
                + ("; I'll do that too when I get there" if then.strip() else "")
                + ". Nothing was restarted -- a message will report when I get there.")
    if not _notices.available():
        # Studio / tests: no one to deliver a background result to, so the
        # search runs inside the tool call, as it always did.
        with _survey.paused():
            out = _approach(description, state, then)
        return out + (_mv.view_stale_note() if _STARTED in out else "")
    return _start_search(bridge, description, name, state, then)


# ── The search runs in the background (2026-10-20) ──────────────────────────
# A search is 1-2 minutes of turning and vision calls. Run inside the tool
# call, it held the whole brain: on Telegram (2026-10-20) "go near the dining
# table" blocked for 2 min, the door's arrival report waited behind it and
# landed AFTER "heading toward the dining table" -- the owner, confused, asked
# again, which cancelled the drive that had just begun. Now the tool returns
# within a few seconds; the search goes on in a thread and its result is a
# notice (services/notices.py) to whoever asked. A quick answer (the person-
# only refusal, MANUAL, a photo-memory hit close by) still comes back as the
# tool's own result.
_QUICK_S = float(os.environ.get("LANGROBO_APPROACH_QUICK_S", "8"))
_STARTED = "On my way; I'll say when I'm there."
_job_lock = threading.Lock()
_job: dict | None = None


def _short_name(description: str) -> str:
    # for the replies: "the orange bottle" -> "orange bottle", so the text does
    # not read "the the orange bottle" (floor test, 2026-09-26)
    # ...and "my bag" -> "bag": "the my bag" was said aloud on 2026-10-12
    return re.sub(r"^(?:(?:the|a|an|my|your|our)\s+)+", "", description.strip(),
                  flags=re.IGNORECASE)


def _join_errands(old: str, new: str) -> str:
    old, new = (old or "").strip(), (new or "").strip()
    if not new or new in old:
        return old
    return f"{old}; then {new}" if old else new


def search_active() -> bool:
    """True while a background search is running (agent_node lets talk without
    a stop word go on, as it does for a drive)."""
    job = _job
    return bool(job and job["thread"].is_alive() and not job["superseded"])


def search_status() -> str | None:
    """'looking for the X (40 s so far)' while a search runs, else None."""
    job = _job
    if not (job and job["thread"].is_alive() and not job["superseded"]):
        return None
    return f"looking for the {job['name']} ({time.time() - job['since']:.0f} s so far)"


def _already_after(bridge, name: str) -> str | None:
    """"searching" / "driving" when the robot is already after `name`."""
    key = name.casefold()
    job = _job
    if job and job["thread"].is_alive() and not job["superseded"] and job["name"].casefold() == key:
        return "searching"
    try:
        state = bridge.navigation_state() or {}
    except Exception:
        return None
    label = ((state.get("goal") or {}).get("label") or "").casefold()
    if state.get("active") and label == f"near the {key}":
        return "driving"
    return None


def _start_search(bridge, description: str, name: str, state: dict, then: str) -> str:
    global _job
    with _job_lock:
        prev = _job
        if prev is not None and prev["thread"].is_alive():
            # Something else was asked for: the old search stops at its next
            # check (it may be inside a vision call), and never drives.
            prev["superseded"] = True
            bridge.request_motion_stop()
        job = {"name": name, "description": description, "then": then, "state": dict(state or {}),
               "requester": _notices.requester_of(state), "since": time.time(),
               "superseded": False, "detached": False, "done": threading.Event(),
               "result": None, "prev": prev}
        job["thread"] = threading.Thread(target=_run_search, args=(job,), daemon=True,
                                         name=f"search:{name[:20]}")
        _job = job
    bridge.cancel_navigation()          # a drive to something else ends here
    job["thread"].start()
    if job["done"].wait(_QUICK_S):
        return job["result"] + (_mv.view_stale_note() if _STARTED in job["result"] else "")
    with _job_lock:
        if job["result"] is not None:   # finished in the gap
            return job["result"] + (_mv.view_stale_note() if _STARTED in job["result"] else "")
        job["detached"] = True
    return (f"Looking for the {name} now -- searching in the background. The result "
            f"(found and on my way, or not found) is reported to the user "
            f"automatically. Tell them you are looking; do NOT say you have found "
            f"it, and do not call this again.")


def _run_search(job: dict) -> None:
    prev = job["prev"]
    job["prev"] = None
    if prev is not None:
        prev["thread"].join(timeout=90.0)
    out = None
    try:
        if not job["superseded"]:
            # No photo survey while searching: the Mac runs one model, and
            # every view of the search waits on it. The views are queued and
            # surveyed after.
            with _survey.paused():
                out = _approach(job["description"], job["state"], job=job)
    except Exception as e:              # noqa: BLE001 -- a thread must report, not die
        logger.exception("background search for %r failed", job["name"])
        out = f"I couldn't finish looking for the {job['name']} ({type(e).__name__})."
    out = out or f"Stopped looking for the {job['name']}."
    if _STARTED not in out and not job["superseded"]:
        from .telegram import send_pending_photo
        send_pending_photo(False)       # a photo held for an arrival that won't come
    with _job_lock:
        job["result"] = out
        detached = job["detached"]
        job["done"].set()
    if not detached or job["superseded"] or out.startswith("Stopped"):
        return                          # the tool returned it, or the person moved on
    stale = _mv.view_stale_note()
    _notices.post(out, job["requester"],
                  report=f"[SYSTEM] The search for {job['description']} finished: {out}{stale}")


def _approach(description: str, state: dict, then: str = "", job: dict | None = None) -> str:
    bridge = _bridge.get()
    description = description.strip()
    name = _short_name(description)
    if _names_a_person_only(description):
        return (f"I can't recognise who people are -- I only see what they look "
                f"like, so I could go to the wrong person. Ask the user what "
                f"\"{description}\" looks like or is wearing, or where they are "
                f"(\"the man in the blue shirt by the sofa\"), then try again "
                f"with that description. I have not moved.")
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
    drove_over = ""     # said when a later step fails: the drive there DID happen

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
                else:
                    drove_over = f"I drove over to where I saw the {name} {age}. "
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
    def circle(skip_ahead: bool, first_line: bool) -> tuple:
        """One full circle from here: (stop_text, uv, capture, views)."""
        nonlocal note, cut_short
        seen = {0} if skip_ahead else set()
        offset, direction = 0, 1
        start = bridge.get_current_pose()
        uv = capture = None
        while uv is None and len(seen) < _SEARCH_STEPS:
            if bridge.motion_interrupted():
                return f"Stopped searching for the {name}.", None, None, len(seen)
            if 0 in seen:
                if Twist is None:
                    break
                k = next(k for k in range(1, _SEARCH_STEPS + 1)
                         if (offset + direction * k * _SEARCH_STEP_DEG) % 360 not in seen)
                now = bridge.get_current_pose()
                drift = 0.0
                if start and now:
                    drift = (offset - (now[2] - start[2]) + 180.0) % 360.0 - 180.0
                if len(seen) == 1 and first_line:
                    if _say(bridge, state, note.strip() or
                            f"I don't see the {name} from here, so I'm looking around."):
                        note = ""                  # said already: not again in the answer
                elif len(seen) == _SEARCH_STEPS // 2:
                    _say(bridge, state, f"Still looking for the {name}.")
                ok, why = _mv.turn_robot(bridge, direction * k * _SEARCH_STEP_DEG + drift)
                if not ok:
                    if why == "interrupted":
                        return f"Stopped searching for the {name}.", None, None, len(seen)
                    if direction == 1:
                        direction = -1            # blocked this way: finish the circle the other way
                        continue
                    cut_short = (why, len(seen))   # a part circle: another spot may still do
                    break
                offset = int(offset + direction * k * _SEARCH_STEP_DEG) % 360
            frame, capture = _capture(bridge)
            if frame is None:
                if bridge.motion_interrupted():
                    return f"Stopped searching for the {name}.", None, None, len(seen)
                return ("My camera feed isn't giving me a fresh image right now, "
                        "so I can't look for it.", None, None, len(seen))
            try:
                uv = _vlm_locate(frame, description)
            except Exception as e:
                return (f"I couldn't analyse the camera image (vision model error: "
                        f"{type(e).__name__}). Try again in a moment.", None, None, len(seen))
            seen.add(offset)
        return None, uv, capture, len(seen)

    spots = 1
    cut_short = None                  # (why, views) when turning was blocked both ways
    if uv is None:
        stop, uv, found_capture, _ = circle(bool(first_view), True)
        if stop:
            return stop
        if uv is not None:
            capture = found_capture

    # ── 3. Not around here: look from other spots in the room ──────────────
    # (owner, 2026-10-13: "go to another place and find it -- whole room
    # search one by one"). The LiDAR says which way is open and not searched
    # yet (utils/viewpoints.py); reach drives there; a full circle there.
    here = bridge.get_current_pose()
    visited = [here[:2]] if here else []
    tries = 0
    while uv is None and Twist is not None and spots < _SEARCH_SPOTS and tries < 2 * _SEARCH_SPOTS:
        tries += 1
        if bridge.motion_interrupted():
            return f"Stopped searching for the {name}."
        pose, pts = bridge.get_current_pose(), bridge.get_scan_points()
        vp = _viewpoints.next_viewpoint(pose, pts, visited)
        if vp is None:
            break
        visited.append(vp[:2])
        _say(bridge, state, f"The {name} isn't around here. I'll look from another spot."
             if spots == 1 else "Not here either. Trying another spot.")
        leg = bridge.reach_and_wait(round(vp[0], 2), round(vp[1], 2), round(vp[2], 1))
        if leg.get("result") == "interrupted":
            return f"Stopped searching for the {name}."
        if not leg.get("ok"):
            continue                       # that way failed: the next pick avoids it
        spots += 1
        drove_over = "I moved to another spot to look. "
        stop, uv, found_capture, _ = circle(False, False)
        if stop:
            return stop
        if uv is not None:
            capture = found_capture

    if uv is None and spots == 1 and cut_short:
        return (note + f"I couldn't turn either way to keep looking for the {name} "
                f"({cut_short[0]}). It isn't in the {cut_short[1]} view(s) I checked.")
    if uv is None:
        where = (f"I searched {spots} spots around the room, looking all around at each"
                 if spots > 1 else "I turned a full circle and looked carefully")
        if last_seen is not None:
            return (note + f"I saw the {name} {_recall.describe_age(last_seen['age_s'])} "
                    f"(photo {last_seen['photo']}), but it isn't there now. {where}, but "
                    f"couldn't find it -- someone may have moved it.")
        return (note + f"{where}, but I couldn't spot the {name} anywhere.")

    res, capture = _ground_retrying(bridge, description, uv, capture)
    if not res.get("ok"):
        reason = res.get("reason", "unknown")
        if reason == "no_reply_from_jetson":
            return ("I can see it, but the depth-grounding service on the "
                    "Jetson isn't answering, so I can't work out where it is "
                    "in the room.")
        # Say what happened, including a drive that did happen: on 2026-10-04
        # the robot drove 2.5 m to a person, lost her when she walked off, and
        # answered "couldn't measure its distance -- it may be reflective".
        help_text = DEPTH_FAIL_HELP.get(reason, f"the depth reading failed ({reason})")
        if reason == "lost_sight":
            return (f"{drove_over}I saw the {name}, but {help_text}, so I "
                    f"have not driven to it.")
        return (f"{drove_over}I can see the {name}, but I couldn't measure how "
                f"far it is: {help_text}. I have not driven to it.")

    obj = res.get("object")
    if placed and obj and at_its_spot:
        moved = math.hypot(obj["x"] - placed["x"], obj["y"] - placed["y"])
        note = ("It's still where I saw it. " if moved <= object_memory.SAME_OBJECT_M
                else f"It has moved about {moved:.1f} m since I last saw it. ")

    goal = res["goal"]
    if job is not None:
        with _job_lock:
            if job["superseded"]:       # asked for something else meanwhile
                return f"Stopped looking for the {name}."
            then = job["then"]          # errands added while it searched
    # Nav completion arrives minutes later as a [SYSTEM] turn — remember who
    # asked so a Telegram-initiated approach reports back to that chat.
    _mv.remember_requester(state, then, target=description)
    bridge.start_nav_to_pose(round(goal["x"], 2), round(goal["y"], 2),
                             round(math.degrees(goal["yaw"]), 1),
                             label=f"near the {name}")
    return (note + f"I can see the {name} — about {res['depth_m']:.1f} m away. "
            + _STARTED)


def arrival_check(description: str) -> str:
    """Is the object really in front of the robot now that it has arrived?
    One fresh photo, asked the same "is it clearly visible?" question as the
    search, measured with depth when it is there. Returns a sentence for the
    arrival report, or "" if the check itself could not run -- a broken
    camera or model must never hold up the report.

    Why: arrival meant "reach got to the goal", not "the thing is here". A
    goal placed from a wrong pixel, or a thing moved while driving, still
    arrived -- and the robot said so (FIND_AND_GO.md, Known limits)."""
    name = re.sub(r"^(?:(?:the|a|an|my|your|our)\s+)+", "", (description or "").strip(), flags=re.IGNORECASE)
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
        res = _ground(bridge, uv, capture, description)
        rel = res.get("relative") if res.get("ok") else None
        if rel:
            dist = math.hypot(rel["forward_m"], rel["left_m"])
            return f" I can see the {name} in front of me, about {dist:.1f} m away."
        return f" I can see the {name} in front of me."
    except Exception:
        return ""


# The scan: SCAN_VIEWS stops a full circle apart. The colour camera sees ~87 deg
# across, so 5 x 72 deg still covers everything with ~15 deg of overlap, and
# saves a whole turn over the old 6 x 60 (each pivot is ~7 s on the real
# robot, and slides the body 3-14 cm: 2026-10-18).
SCAN_VIEWS = int(os.environ.get("LANGROBO_SCAN_VIEWS", "5"))

# One vision call over all the scan's views, each labelled with where it
# points relative to where the robot faced when the scan began. Before this
# (real robot, 2026-10-18) "look around and tell me what is in this room" was
# answered from ONE forward photo, and a scan ended with "I took 6 photos":
# the user heard nothing about the room.
_SCAN_PROMPT = (
    "You are a home robot. You just turned a full circle in place and took the "
    "photos below, one per direction. Each is labelled with where it points. "
    "Describe the room around you for a person who cannot see it: the main "
    "furniture and objects, and which side each is on (ahead, left, behind, "
    "right). Mention a doorway or an open path if you see one. Plain words, at "
    "most three short sentences, no lists. Say only what the photos show."
)


def _direction(rel_deg: float) -> str:
    """Where a view points, relative to the heading the scan started from (CCW +)."""
    a = (rel_deg + 180.0) % 360.0 - 180.0
    if abs(a) <= 30:
        return "ahead"
    if abs(a) >= 150:
        return "behind"
    side = "left" if a > 0 else "right"
    if abs(a) < 75:
        return f"ahead-{side}"
    if abs(a) > 105:
        return f"behind-{side}"
    return side


def describe_scan(views: list) -> str | None:
    """views: [(jpeg, degrees turned since the scan began)]. One VLM call on the
    vision-tool slot. None when there is nothing to describe or the model fails
    (the scan itself still counts: the map and the photo log have the views)."""
    views = [(j, d) for j, d in views if j]
    if not views:
        return None
    parts = [{"type": "text", "text": _SCAN_PROMPT}]
    for jpeg, deg in views:
        parts.append({"type": "text", "text": f"Photo pointing {_direction(deg)}:"})
        parts.append(_recall._image_part(jpeg))
    try:
        _, _, text = _recall._invoke(_survey.vision_tool_llm(), parts, run_name="scan_describe")
    except Exception as e:                      # noqa: BLE001 -- a description is a bonus
        logger.warning("scan_describe failed: %s", e)
        return None
    text = " ".join((text or "").split())
    return text or None


@tool
def scan_surroundings(describe: bool = True,
                      state: Annotated[dict | None, InjectedState] = None) -> str:
    """Turn a full circle in place, photograph every direction and say what is
    around the robot. Use for "look around", "what is in this room", "scan the
    room". describe=False only fills the map (before driving somewhere unseen).

    Takes about 45 seconds; returns a short description of the room."""
    refusal = _mv.blocked_by_role(state) or _mv.blocked_by_manual()   # MANUAL: a turn against its zeros goes nowhere
    if refusal:
        return refusal
    bridge = _bridge.get()
    bridge.clear_motion_stop()

    try:
        from geometry_msgs.msg import Twist  # noqa: F401 -- the timed fallback needs it
    except ImportError:
        return "I can't turn right now — my wheel interface isn't available."
    step = 360.0 / SCAN_VIEWS
    views = []
    # The view it already has counts: the first photo is taken BEFORE turning.
    frame, _ = _capture(bridge, settle_s=1.0, source="scan")
    views.append((frame, 0.0))
    for i in range(1, SCAN_VIEWS + 1):
        ok, why = _mv.turn_robot(bridge, step)
        if not ok:
            if why == "interrupted":
                return "Scan stopped."
            # Said so it cannot be read as success: "Scan stopped after 0
            # degrees" got "I have scanned the area around me" back from the
            # model 3 times in 5 (a Telegram "Now check", 2026-10-02); this
            # wording, 5 of 5 honest.
            if i == 1:
                return (f"I could NOT look around: I could not turn at all ({why}). "
                        f"Nothing was scanned. Tell the user that, and why.")
            seen = describe_scan(views) if describe else None
            return (f"I only looked around {round((i - 1) * step)} of 360 degrees, then "
                    f"could not turn further ({why}). Tell the user the scan is incomplete."
                    + (f" What I did see: {seen}" if seen else "") + _mv.view_stale_note())
        if i == SCAN_VIEWS:
            break                               # back where it began: that view is views[0]
        # Pause so vSLAM/nvblox integrate a still frame (motion blur hurts both).
        end = time.time() + 1.0
        while time.time() < end:
            if bridge.motion_interrupted():
                return "Scan stopped."
            time.sleep(0.05)
        # And keep the view: every photo goes into the photo log, so "where
        # is my bag?" after "look around" is answered from these.
        frame, _ = _capture(bridge, settle_s=1.0, source="scan")
        views.append((frame, i * step))
    photos = sum(1 for f, _ in views if f)
    seen = describe_scan(views) if describe else None
    if seen:
        return f"Scan complete, all the way round. What is around me: {seen}"
    return (f"Scan complete — I turned a full circle, so the map now covers all "
            f"around me, and I took {photos} photo(s)"
            + (" (I could not get a description of them right now)" if describe else "")
            + "." + _mv.view_stale_note())


@tool
def list_saved_locations() -> str:
    """List the named places the robot can navigate to with navigate_to_pose —
    the spots saved with save_location."""
    known = _bridge.get().get_known_locations()
    if not known:
        return "No locations saved yet. Stand me somewhere and say 'save this location as ...'."
    return "I can go to: " + ", ".join(sorted(known.keys())) + "."
