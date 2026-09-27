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
  * On its own llama.cpp slot (LANGROBO_SURVEY_SLOT, default 3 -- the free one),
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

SURVEY_SLOT = int(os.environ.get("LANGROBO_SURVEY_SLOT", "3"))
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
stats = {"photos": 0, "objects": 0, "skipped": 0, "errors": 0}


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
    if not frame or not stamp:
        return False
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
        while _busy_probe() or _paused:        # a turn or search is running
            time.sleep(0.5)
        try:
            survey_photo(rec)
        except Exception as e:                 # a survey must never kill the thread
            stats["errors"] += 1
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
    """Ask the vision model what is in the photo: [(label, box px)]."""
    import base64
    import io

    from langchain_core.messages import HumanMessage
    from PIL import Image

    from ..services.llm import get_llm
    from ..utils.speech_stream import strip_thought_residue

    width, height = Image.open(io.BytesIO(frame)).size
    b64 = base64.b64encode(frame).decode()
    llm = get_llm("local_agent", slot=SURVEY_SLOT, streaming=False, max_tokens=600)
    reply = llm.invoke([HumanMessage(content=[
        {"type": "text", "text": _PROMPT.format(n=MAX_OBJECTS)},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ])], config={"run_name": "photo_survey", "tags": ["photo_survey"]})
    return parse_objects(strip_thought_residue(str(reply.content)), width, height)


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
    placed = 0
    for label, box in list_objects(rec["frame"]):
        u, v = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        res = bridge.ground_pixel(u, v, stamp=rec["stamp"], box=box)
        if not res.get("ok") or not res.get("at_capture"):
            continue                           # depth gone, or not from the photo
        obj = res.get("object") or {}
        try:
            object_memory.remember(
                label, obj["x"], obj["y"], rec["pose"], epoch, when=rec["when"],
                depth_m=res.get("depth_m"), at_capture=True,
                region=bool(res.get("region")), source=f"survey:{rec['source']}")
            placed += 1
        except (OSError, KeyError, TypeError, ValueError):
            continue
    stats["photos"] += 1
    stats["objects"] += placed
    logger.info("photo survey (%s): %d object(s) placed", rec["source"], placed)
    return placed
