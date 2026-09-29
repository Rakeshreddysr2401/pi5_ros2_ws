"""Photo survey — every photo the robot takes becomes object memory.

Before this, only the ONE object a search was asked for was ever remembered:
the robot could look straight at a white box, a bag and a chair while hunting
for a bottle and afterwards know nothing about any of them, so "go to the
chair" started a fresh search (owner, 2026-09-27: "it always rotates 90
degrees and misses items").

Now every photo -- look(), every search view, locate_object -- is submitted
here with what makes it usable later from anywhere:

    jpeg + camera stamp + robot pose (x, y, heading) + wall-clock time + odom epoch

In the background the vision model lists the objects in it, each with a box,
and the Jetson places every one in the room using THAT PHOTO's depth and
camera pose (pixel_to_goal's snapshot for the stamp, held the moment the photo
was taken -- bridge.hold_frame). The result lands in object memory in odom, so
a later "go to the chair" works out bearing and distance from where the robot
is NOW, however far it has moved since the photo (approach.py step 1, and the
orange dots in RViz).

    photo ──▶ VLM: list objects + boxes ──▶ per box: Jetson ground at the
    photo's stamp ──▶ object (x, y) in odom ──▶ object_memory.remember

Rules that keep it from getting in the way:
  * It runs only while the brain is idle and no search is running (the Mac
    runs one model; a survey competing with a turn slows the turn).
    set_busy_probe() is how agent_node says "a turn is running"; approach
    wraps its search in paused().
  * And it GIVES WAY mid-photo: a turn that starts during the VLM call
    cancels it (the connection closes and llama.cpp stops generating), and
    one that starts while the objects are being placed stops the placing.
    The photo goes back to the front of the queue -- with its object list
    if it already has one, so the VLM is not asked twice -- and resumes once
    the brain is idle. Before this, a survey that had just started made the
    next reply wait up to ~40 s for it.
  * Each object remembers WHICH photo placed it (`photo`: the camera stamp),
    so "go near it" about a photo the survey has done needs no VLM call at
    all (approach._in_conversation_photos).
  * On the vision-TOOL slot (VISION_TOOL_SLOT, default 3; needs --parallel 4),
    never local_agent's: that slot holds the vision conversation and its
    photos. No such slot on the server -> the survey is off (fit_slot),
    unstreamed (streamed, this server files marked-up answers as hidden text).
  * Only photo-time grounding is kept (at_capture): an object placed with
    depth taken after the robot moved would be in the wrong place.
  * A photo with no camera stamp (Studio test image) is not surveyed.

The Jetson keeps SNAPSHOTS photos (24); a queue longer than that would only
survey photos whose depth is gone, so the queue is bounded below it.
"""

import json
import logging
import os
import re
import threading
import time
from collections import deque

from ..services import object_memory
from . import _bridge

logger = logging.getLogger(__name__)

# ── The vision-TOOL slot ────────────────────────────────────────────────────
# One-shot vision calls -- the survey's object list and approach/locate's
# "where is X in this photo" -- each send ONE photo with a short prompt that
# has nothing to do with the conversation. They run local_agent's model, but
# NEVER on local_agent's slot (1): that slot holds the vision conversation --
# its prompt, its tools and the earlier photos it reasons over ("is it still
# there?", "what colour was it?"). A one-shot call there overwrote all of it,
# up to 8 times in one search, and the next vision turn re-read every kept
# photo through the vision encoder. So they share slot 3 (--parallel 4),
# one at a time (approach pauses the survey while it searches).
VISION_TOOL_SLOT = int(os.environ.get("LANGROBO_VISION_TOOL_SLOT",
                                      os.environ.get("LANGROBO_SURVEY_SLOT", "3")))
_agent_vision_slot = None     # local_agent's slot, set by fit_slot: locate's fallback
MAX_QUEUE = 12               # < the Jetson's SNAPSHOTS (24): depth still held
MAX_OBJECTS = 8
_MAX_PHOTO_AGE_S = 900.0     # an older photo's snapshot is long gone anyway

_PROMPT = (
    "List the distinct physical objects you can see in this image: furniture, "
    "bags, boxes, bottles, appliances, toys, doors and the like. Not the floor, "
    "walls, ceiling or windows. At most {n}, the most prominent first. Give "
    "each a short name with its colour (e.g. \"white chair\", \"black bag\"). "
    "Reply with ONLY a JSON object, no other text: "
    '{{"objects": [{{"label": "white chair", "box": [ymin, xmin, ymax, xmax]}}]}}. '
    "Each box is the tight bounding box of the whole object, in normalized "
    "image coordinates from 0 to 1000, where (0,0) is the top-left corner. "
    'If there are no objects, reply {{"objects": []}}.'
)

# Surfaces, not things: they have no single position to drive to.
_SKIP = {"floor", "wall", "walls", "ceiling", "ground", "window", "windows",
         "carpet", "rug", "light", "shadow", "room", "tile", "tiles", "curtain"}

_queue: deque = deque(maxlen=MAX_QUEUE)
_cv = threading.Condition()
_paused = 0
_thread: threading.Thread | None = None
_busy_probe = lambda: False       # noqa: E731 -- set by agent_node
stats = {"photos": 0, "objects": 0, "skipped": 0, "errors": 0, "yielded": 0,
         "last_error": None, "last_photo_at": None}


class Yielded(Exception):
    """A turn or search started: this photo was put back to finish later."""


def _should_yield() -> bool:
    return bool(_paused) or _busy_probe()


def status() -> dict:
    """For the health API: counters so far, and what is waiting."""
    with _cv:
        queued, paused_now = len(_queue), bool(_paused)
    return {**stats, "queued": queued, "paused": paused_now,
            "slot": VISION_TOOL_SLOT, "enabled": VISION_TOOL_SLOT is not None,
            "worker_alive": bool(_thread and _thread.is_alive())}


def fit_slot(total_slots: int | None, agent_vision_slot: int | None) -> int | None:
    """Fit VISION_TOOL_SLOT to the server agent_node found (called once, after
    its slot probe). Returns the slot one-shot vision calls will use, or None:
    the server has no slot VISION_TOOL_SLOT (fewer than --parallel 4). Then
      * the photo survey is OFF -- it is a background extra, and running it
        on an agent's slot would evict that agent's cache every photo;
      * locate/approach, which the user is waiting on, use local_agent's slot
        (the pre-2026-09-29 behaviour: it works, at the cost of that cache).
    A failed probe (None) leaves everything as configured."""
    global VISION_TOOL_SLOT, _agent_vision_slot
    _agent_vision_slot = agent_vision_slot
    if total_slots and VISION_TOOL_SLOT is not None and VISION_TOOL_SLOT >= total_slots:
        VISION_TOOL_SLOT = None
    return VISION_TOOL_SLOT


def vision_tool_llm(**overrides):
    """local_agent's model for a one-shot, unstreamed vision call, on the
    vision-tool slot (see VISION_TOOL_SLOT). Imported at call time so tests
    can swap services.llm.get_llm."""
    from ..services.llm import get_llm
    slot = VISION_TOOL_SLOT if VISION_TOOL_SLOT is not None else _agent_vision_slot
    return get_llm("local_agent", slot=slot, streaming=False, **overrides)


def set_busy_probe(probe) -> None:
    """probe() -> True while a turn is running (agent_node)."""
    global _busy_probe
    _busy_probe = probe


class paused:
    """`with survey.paused():` -- no survey runs inside (approach's search)."""

    def __enter__(self):
        global _paused
        with _cv:
            _paused += 1

    def __exit__(self, *exc):
        global _paused
        with _cv:
            _paused -= 1
            _cv.notify_all()


def submit(frame: bytes, stamp, pose, source: str, when: float | None = None,
           epoch=None) -> bool:
    """Queue a photo for surveying. The caller has already asked the Jetson to
    hold its depth (hold_frame). False when it cannot be used (no stamp)."""
    if not frame or not stamp or VISION_TOOL_SLOT is None:
        return False                   # no stamp to ground on / no slot of its own
    rec = {"frame": frame, "stamp": tuple(stamp), "pose": pose, "source": source,
           "when": time.time() if when is None else when, "epoch": epoch}
    with _cv:
        _queue.append(rec)             # maxlen drops the oldest
        _ensure_thread()
        _cv.notify_all()
    return True


def _ensure_thread() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_worker, name="photo-survey", daemon=True)
        _thread.start()


def _worker() -> None:
    while True:
        with _cv:
            while not _queue or _paused:
                _cv.wait(timeout=1.0)
            rec = _queue.popleft()
        while _should_yield():                 # a turn or search is running
            time.sleep(0.5)
        try:
            survey_photo(rec)
        except Yielded:
            stats["yielded"] += 1
            with _cv:
                if len(_queue) < MAX_QUEUE:    # full: newer photos win
                    _queue.appendleft(rec)     # first in line once idle
        except Exception as e:                 # a survey must never kill the thread
            stats["errors"] += 1
            stats["last_error"] = f"{type(e).__name__}: {e}"[:200]
            logger.warning("photo survey failed (%s): %s", rec["source"], e)


def parse_objects(text: str, width: int, height: int) -> list:
    """The VLM's reply -> [(label, (x0, y0, x1, y1) in pixels)]. Pure."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for o in (data.get("objects") or [])[:MAX_OBJECTS]:
        try:
            label = " ".join(str(o["label"]).lower().split())
            ymin, xmin, ymax, xmax = (float(a) for a in o["box"])
        except (KeyError, TypeError, ValueError):
            continue
        if not label or label.split()[-1] in _SKIP:     # "white wall" is a wall
            continue
        if not (all(0 <= a <= 1000 for a in (ymin, xmin, ymax, xmax))
                and xmin < xmax and ymin < ymax):
            continue
        sx, sy = width / 1000.0, height / 1000.0
        out.append((label, (xmin * sx, ymin * sy, xmax * sx, ymax * sy)))
    return out


def list_objects(frame: bytes) -> list:
    """Ask the vision model what is in the photo: [(label, box px)].
    Raises Yielded if a turn starts while it is waiting on the model."""
    import asyncio
    import base64
    import io

    from langchain_core.messages import HumanMessage
    from PIL import Image

    from ..utils.speech_stream import strip_thought_residue

    width, height = Image.open(io.BytesIO(frame)).size
    b64 = base64.b64encode(frame).decode()
    llm = vision_tool_llm(max_tokens=600)
    msgs = [HumanMessage(content=[
        {"type": "text", "text": _PROMPT.format(n=MAX_OBJECTS)},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ])]
    reply = asyncio.run(_invoke_unless_needed(llm, msgs))
    return parse_objects(strip_thought_residue(str(reply.content)), width, height)


async def _invoke_unless_needed(llm, msgs):
    """llm.ainvoke, cancelled the moment a turn wants the Mac. Cancelling
    the task closes the HTTP connection, and llama.cpp drops a request whose
    client has gone -- so the model is free for the turn at once, not after
    this photo's ~5-40 s."""
    import asyncio
    task = asyncio.ensure_future(llm.ainvoke(
        msgs, config={"run_name": "photo_survey", "tags": ["photo_survey"]}))
    while not task.done():
        if _should_yield():
            task.cancel()
            try:
                await task
            except BaseException:          # CancelledError, or it failed anyway
                pass
            raise Yielded()
        await asyncio.wait({task}, timeout=0.2)
    return task.result()


def survey_photo(rec: dict) -> int:
    """List, ground and remember everything in one photo. Returns how many
    objects were placed in memory."""
    bridge = _bridge.get()
    epoch = bridge.get_origin_epoch()
    if rec.get("epoch") is not None and epoch != rec["epoch"]:
        stats["skipped"] += 1                  # odom restarted: the pose means nothing now
        return 0
    if time.time() - rec["when"] > _MAX_PHOTO_AGE_S:
        stats["skipped"] += 1
        return 0
    if "objects" not in rec:                   # not already listed before a yield
        rec["objects"] = list_objects(rec["frame"])
    while rec["objects"]:
        if _should_yield():
            raise Yielded()                    # the rest is placed after the turn
        label, box = rec["objects"].pop(0)
        u, v = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        res = bridge.ground_pixel(u, v, stamp=rec["stamp"], box=box)
        if not res.get("ok") or not res.get("at_capture"):
            continue                           # depth gone, or not from the photo
        obj = res.get("object") or {}
        try:
            object_memory.remember(
                label, obj["x"], obj["y"], rec["pose"], epoch, when=rec["when"],
                depth_m=res.get("depth_m"), at_capture=True,
                region=bool(res.get("region")), source=f"survey:{rec['source']}",
                photo=list(rec["stamp"]))
            rec["placed"] = rec.get("placed", 0) + 1
        except (OSError, KeyError, TypeError, ValueError):
            continue
    placed = rec.get("placed", 0)
    stats["photos"] += 1
    stats["last_photo_at"] = time.time()
    stats["objects"] += placed
    logger.info("photo survey (%s): %d object(s) placed", rec["source"], placed)
    return placed
