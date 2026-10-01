"""photo_recall — the ask_photos tool: answer a question from every photo the robot took recently.

The robot's short-term memory is its photos (tools/photos.py), not a list of
objects at coordinates (owner, 2026-10-01). Any agent asks one question; the
vision model reads ALL logged photos at once and says which photo answers it
and where in that photo; the Jetson places that spot in the room with THAT
photo's depth and camera pose; and the distance and direction are worked out
from where the robot is NOW, however far it has moved since.

    photo log (oldest first) + question ──▶ VLM: {"answer", "object", "photo", "box"}
        └─ photo picked ──▶ same request + that photo again: "the tight box"
              └─▶ Jetson ground_pixel(box, stamp of THAT photo) ──▶ room x, y
                    └─▶ distance + bearing from the pose now

Measured on the rover 2026-10-01 (8 views 45 deg apart, Gemma 4 12B): the
right photo 8 of 9 times, including "a red cup" -> none, and "something I can
sit on" -> the office chair; a small flat keyboard was the miss. Boxes were
good left-right and loose up-down -- one backpack box landed on the floor
below it in the 8-photo request and was right when asked of that photo alone,
hence step 2.

KV CACHE. The instructions, then the photos in log order, each labelled with
its number only -- no age, no "minutes ago" -- so consecutive questions share
the whole prefix and the server reads only the question (~4 s, against ~20 s
for 8 photos cold). Ages go into the TOOL's reply, computed here, never into
the prompt. On the vision-tool slot (survey.VISION_TOOL_SLOT), never an agent's.

THE REWIND LIMIT. Step 2 appends to step 1's request (the photo again + the
box question), so the NEXT question must rewind the slot past all of that to
the shared photo prefix. Measured 2026-10-02 on the Mac (Gemma's sliding-window
cache, server without --swa-full): a rewind of <= ~450 tokens keeps the cache,
>= ~600 throws it all away (17 s to re-read 8 photos). Hence the instructions
BEFORE the photos, only "Question: ..." after them, a short box prompt and a
capped answer: question + answer + photo + box prompt stay under ~420 tokens.
`--swa-full` on the server removes the limit.

The VLM is never asked for a distance or a position: it cannot know one. It
picks a photo and a box; depth and pose come from the sensors.
"""

import base64
import io
import json
import logging
import math
import os
import re
import time

from . import _bridge
from . import photos as _photos
from . import survey as _survey

logger = logging.getLogger(__name__)

# The colour camera's horizontal field of view: a pixel column's angle from
# the photo's heading when its depth is gone (bearing-only answers).
HFOV_DEG = float(os.environ.get("LANGROBO_COLOR_HFOV_DEG", "87"))
# Moved further than this since the photo: a bearing-only answer, taken from
# where the photo was, no longer says where to turn from here.
_BEARING_ONLY_MAX_MOVE_M = 0.5

# Before the photos: static, so it is part of the cached prefix.
_ASK_INSTRUCTIONS = (
    "Below are photos you, a home robot, took recently, numbered in the order "
    "you took them. After them comes a question. "
    "Answer from the photos only. Reply with ONLY a JSON object, no other text: "
    '{{"answer": "<one short sentence>", "object": "<the thing the question is '
    'about, short, or null>", "photo": <the number of the photo that shows it '
    'best, or null>, "box": [ymin, xmin, ymax, xmax] or null}}. '
    "The box is the tight bounding box of that object in THAT photo, "
    "normalized 0-1000 with (0,0) the top-left. If none of the photos shows "
    'it, say so in "answer" and give "photo": null.'
)
_QUESTION = "Question: {question}"          # after the photos: keep it this short
# One photo: asked as "is it clearly visible?", not "where is it in photo n?"
# -- that wording made the model point at SOMETHING (2026-10-02: "the
# cardboard box" in a photo with none -> the toy car). This one: 8/8 on the
# rover's photos, 4 present and 4 absent.
_QUESTION_IN = ("Question: is {what} clearly visible in photo {n}? Only photo {n} "
                "counts; if it is not clearly there, photo is null.")
# Short on purpose (THE REWIND LIMIT).
_BOX_PROMPT = 'Photo {n} again. JSON only: {{"box": [ymin, xmin, ymax, xmax]}} of the {obj}, or {{"box": null}}.'
_ANSWER_MAX_TOKENS = 120


# ── pure parts ──────────────────────────────────────────────────────────────

def _image_part(jpeg: bytes) -> dict:
    return {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def photo_parts(log: list) -> list:
    """The cached prefix: the instructions, then every logged photo, oldest
    first, labelled with its number only. Identical for the same log."""
    parts = [{"type": "text", "text": _ASK_INSTRUCTIONS}]
    for rec in log:
        parts.append({"type": "text", "text": f"Photo {rec['n']}:"})
        parts.append(_image_part(rec["jpeg"]))
    return parts


def _json_in(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_box(raw, width: int, height: int) -> tuple | None:
    """[ymin, xmin, ymax, xmax] in 0-1000 -> (x0, y0, x1, y1) pixels, or None."""
    try:
        ymin, xmin, ymax, xmax = (float(a) for a in raw)
    except (TypeError, ValueError):
        return None
    if not (all(0 <= a <= 1000 for a in (ymin, xmin, ymax, xmax)) and xmin < xmax and ymin < ymax):
        return None
    sx, sy = width / 1000.0, height / 1000.0
    return (xmin * sx, ymin * sy, xmax * sx, ymax * sy)


def parse_answer(text: str, known: set) -> dict:
    """The VLM's reply -> {"answer", "object", "photo" (a known number or
    None), "box_raw"}. A photo number that is not in the log is no photo."""
    data = _json_in(text) or {}
    photo = data.get("photo")
    try:
        photo = int(photo) if photo is not None else None
    except (TypeError, ValueError):
        photo = None
    if photo not in known:
        photo = None
    answer = str(data.get("answer") or "").strip()
    if not answer and not data:
        answer = (text or "").strip()[:200]        # not JSON: keep what it said
    obj = data.get("object")
    return {"answer": answer, "object": str(obj).strip() if obj else None,
            "photo": photo, "box_raw": data.get("box") if photo is not None else None}


def pixel_angle_deg(u: float, width: int, hfov_deg: float = HFOV_DEG) -> float:
    """A pixel column's angle from the camera's heading, + to the LEFT
    (pinhole: the image centre is straight ahead)."""
    fx = (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    return math.degrees(math.atan2(width / 2.0 - u, fx))


def wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def describe_age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "just now" if s < 10 else f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} h {s % 3600 // 60} min ago"


# ── the question ────────────────────────────────────────────────────────────

def _invoke(llm, content: list, history: list | None = None, run_name: str = "ask_photos"):
    from langchain_core.messages import HumanMessage

    from ..utils.speech_stream import strip_thought_residue
    msgs = list(history or []) + [HumanMessage(content=content)]
    reply = llm.invoke(msgs, config={"run_name": run_name, "tags": [run_name]})
    return msgs, reply, strip_thought_residue(str(reply.content))


def ask(question: str, only: int | None = None, place: bool = True) -> dict:
    """Answer `question` from the photo log. Pure of LangChain tool plumbing so
    approach.py can use it too. Returns
      {"ok": False, "why": "no_photos"}                      nothing logged
      {"ok": True, "answer", "object", "photo": None, "count", "oldest_s"}
      {"ok": True, ..., "photo": n, "age_s", "box", "placed": {...} | None,
       "distance_m", "bearing_deg", "bearing_only", "ground_reason"}
    bearing_deg is from the robot NOW (0 ahead, + left).
    only: ask about that one photo (still sent with the whole log, for the
    cache); an answer naming another photo counts as not found.
    place=False: stop at the box, do not ground (the caller grounds itself).

    Step 2 (that photo again, for a tight box) runs only for a question over
    the WHOLE log, where the box sets the distance. About one photo it buys
    nothing: measured 2026-10-02 on the rover's 8 photos, 6/9 with it and 6/9
    without, 8.9 s against 5.0 s a question. (Asking for no answer sentence
    saved 0.3 s and missed a spray can it otherwise found: the sentence stays.)"""
    from langchain_core.messages import AIMessage
    from PIL import Image

    bridge = _bridge.get()
    epoch = bridge.get_origin_epoch()
    log = _photos.log(epoch)
    if not log:
        return {"ok": False, "why": "no_photos"}
    now = time.time()
    llm = _survey.vision_tool_llm(max_tokens=_ANSWER_MAX_TOKENS)
    tail = (_QUESTION_IN.format(n=only, what=question.strip()) if only is not None
            else _QUESTION.format(question=question.strip()))
    content = photo_parts(log) + [{"type": "text", "text": tail}]
    with _survey.paused():                 # one request on the vision-tool slot at a time
        msgs, reply, text = _invoke(llm, content)
        got = parse_answer(text, {r["n"] for r in log})
        if only is not None and got["photo"] != only:
            got["photo"] = None
        out = {"ok": True, "answer": got["answer"], "object": got["object"],
               "photo": got["photo"], "count": len(log),
               "oldest_s": now - log[0]["when"]}
        if got["photo"] is None:
            return out
        rec = next(r for r in log if r["n"] == got["photo"])
        width, height = Image.open(io.BytesIO(rec["jpeg"])).size
        box = parse_box(got["box_raw"], width, height)
        # Step 2: that photo again, appended (the photo prefix stays cached).
        if only is None and got["object"]:
            obj = got["object"]
            try:
                _, _, text2 = _invoke(
                    llm, [_image_part(rec["jpeg"]),
                          {"type": "text", "text": _BOX_PROMPT.format(n=rec["n"], obj=obj)}],
                    history=msgs + [AIMessage(content=str(reply.content))], run_name="ask_photos_box")
                data2 = _json_in(text2) or {}
                if "box" in data2:
                    box = parse_box(data2.get("box"), width, height) or box
            except Exception as e:             # the first box still stands
                logger.warning("ask_photos box step failed: %s", e)
    out.update(age_s=now - rec["when"], box=box, width=width)
    if place:
        out.update(_place(bridge, rec, box, width))
    return out


def locate_in(jpeg: bytes, description: str):
    """Where `description` is in THIS logged photo: (u, v, box) in pixels, as
    approach._vlm_locate returns, or None (not in it). Raises LookupError if
    the photo is not in the current log -- the caller then asks the one photo
    on its own. Goes through the whole log so the cache survives."""
    rec = _photos.lookup(jpeg)
    bridge = _bridge.get()
    if rec is None or rec["n"] not in {r["n"] for r in _photos.log(bridge.get_origin_epoch())}:
        raise LookupError("photo not logged")
    r = ask(description, only=rec["n"], place=False)
    box = r.get("box")
    if r.get("photo") is None or box is None:
        return None
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0, box)


def _place(bridge, rec: dict, box, width: int) -> dict:
    """Where the boxed thing is from the robot NOW: grounded with the photo's
    own depth and pose, or -- that depth gone -- a direction only."""
    res = {"placed": None, "distance_m": None, "bearing_deg": None,
           "bearing_only": False, "ground_reason": None}
    if box is None:
        return res
    u, v = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    pose = bridge.get_current_pose()
    g = bridge.ground_pixel(u, v, stamp=rec["stamp"], box=box)
    if g.get("ok") and g.get("at_capture", True) and g.get("object") and pose:
        ox, oy = g["object"]["x"], g["object"]["y"]
        dx, dy = ox - pose[0], oy - pose[1]
        res.update(placed={"x": ox, "y": oy, "goal": g.get("goal"), "depth_m": g.get("depth_m")},
                   distance_m=math.hypot(dx, dy),
                   bearing_deg=wrap_deg(math.degrees(math.atan2(dy, dx)) - pose[2]))
        return res
    res["ground_reason"] = g.get("reason") or ("from_newer_depth" if g.get("ok") else "unknown")
    # Direction only: the photo's heading plus the pixel's angle -- valid from
    # here only if the robot has not wandered off since the photo.
    then = rec.get("pose")
    if then and pose and math.hypot(pose[0] - then[0], pose[1] - then[1]) <= _BEARING_ONLY_MAX_MOVE_M:
        res.update(bearing_deg=wrap_deg(then[2] + pixel_angle_deg(u, width) - pose[2]),
                   bearing_only=True)
    return res


def describe(result: dict) -> str:
    """The tool's reply: plain sentences, because it may be spoken as it is."""
    from .locate import describe_bearing
    if not result.get("ok"):
        return ("I haven't taken any photos since my position was last reset, so "
                "I have nothing to look back at. I can take a photo now.")
    if result.get("photo") is None:
        n = result["count"]
        return (f"{result['answer']} I checked the {n} photo{'s' if n != 1 else ''} I "
                f"have, going back to {describe_age(result['oldest_s'])}, and it is in "
                f"none of them. I haven't looked around just now.")
    s = f"{result['answer']} I saw it {describe_age(result['age_s'])}, in photo {result['photo']}."
    if result.get("distance_m") is not None:
        s += (f" From where I am now it is about {result['distance_m']:.1f} m away, "
              f"{describe_bearing(result['bearing_deg'])}.")
    elif result.get("bearing_only"):
        s += (f" I can't measure its distance any more, but it is "
              f"{describe_bearing(result['bearing_deg'])}.")
    else:
        s += " I can't tell where it is from here any more."
    return s


# ── the tool ────────────────────────────────────────────────────────────────

from langchain_core.tools import tool  # noqa: E402


@tool
def ask_photos(question: str) -> str:
    """Answer a question from every photo the robot took this session, without moving or looking again: which photo shows it, how long ago, and how far and which way it is from here now.
    Use for "where did you see my bag?", "was there a cup on the table?",
    "what was under the bed?". It checks the photos only: something not in
    any of them may still be in the room -- to check, look again or search."""
    try:
        return describe(ask(question))
    except Exception as e:
        logger.warning("ask_photos failed: %s", e)
        return (f"I couldn't check my photos just now (vision model error: "
                f"{type(e).__name__}). Try again in a moment.")
