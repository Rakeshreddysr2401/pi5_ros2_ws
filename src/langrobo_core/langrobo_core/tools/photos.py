"""Photo log — every photo the robot took: the image, when, from where, and
the camera stamp the Jetson holds its depth under.

THE ROBOT'S SHORT-TERM MEMORY (owner, 2026-10-01: "don't save like 'bag at
x,y' -- the VLM can reason from the frames and give the location"). Every
photo from any source -- look(), a search view, locate, an arrival check --
is logged here, numbered, and any agent can ask about all of them at once
(tools/photo_recall.py): the vision model says WHICH photo shows the thing and
where in it, and the Jetson places it in the room with THAT photo's depth and
camera pose, so it can be reached from wherever the robot is now.

"Go near it" after "what do you see?" means THE THING IN THAT PHOTO; the
conversation carries the image but not where the robot was or which depth
frame belongs to it, so in_conversation() finds those here by the JPEG.

APPEND-ONLY, for the vision model's cache. ask_photos sends the whole log,
oldest first, on every question; llama.cpp then re-reads only photos it has
not seen (measured 2026-10-01: 8 photos, 1890 tokens read once in 19.6 s,
every later question ~90 tokens in ~4 s). So a known photo is never moved,
and old ones are dropped DROP_BLOCK at a time when the log passes MAX_PHOTOS:
one re-read every DROP_BLOCK photos instead of one on every new photo.

Bounded by the Jetson's snapshot store (SNAPSHOTS = 24): a photo older than
that has no depth left to place anything with.
"""

import base64
import hashlib
import threading
from collections import OrderedDict

MAX_PHOTOS = 24          # = the Jetson's SNAPSHOTS
DROP_BLOCK = 8           # dropped together, oldest first (see the docstring)

_lock = threading.Lock()
_by_hash: "OrderedDict[str, dict]" = OrderedDict()
_counter = 0             # photo numbers: never reused within a process


def _key(jpeg: bytes) -> str:
    return hashlib.sha1(jpeg).hexdigest()


def record(jpeg: bytes, stamp, pose, when: float, epoch, source: str) -> dict | None:
    """Log a photo. No stamp -> nothing to ground against, not logged.
    A photo already logged keeps its number and place. Returns its record."""
    global _counter
    if not jpeg or not stamp:
        return None
    key = _key(jpeg)
    with _lock:
        if key in _by_hash:
            return _by_hash[key]
        _counter += 1
        rec = {"n": _counter, "jpeg": jpeg, "stamp": tuple(stamp), "pose": pose,
               "when": when, "epoch": epoch, "source": source}
        _by_hash[key] = rec
        if len(_by_hash) > MAX_PHOTOS:
            for _ in range(min(DROP_BLOCK, len(_by_hash))):
                _by_hash.popitem(last=False)
        return rec


def log(epoch=None) -> list:
    """The logged photos, oldest first. With an epoch, only photos taken under
    it: a pose from another odom origin points at nothing."""
    with _lock:
        recs = list(_by_hash.values())
    if epoch is None:
        return recs
    return [r for r in recs if r.get("epoch") == epoch]


def get(n: int) -> dict | None:
    with _lock:
        for r in _by_hash.values():
            if r["n"] == n:
                return r
    return None


def clear() -> None:
    """Tests only."""
    with _lock:
        _by_hash.clear()


def lookup(jpeg: bytes) -> dict | None:
    with _lock:
        return _by_hash.get(_key(jpeg))


def in_conversation(messages: list, limit: int = 2) -> list:
    """The newest photos in the conversation that the register knows:
    [(jpeg, record)], newest first, at most `limit`."""
    out = []
    for msg in reversed(messages or []):
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            if not (isinstance(part, dict) and part.get("type") == "image_url"):
                continue
            url = (part.get("image_url") or {}).get("url", "")
            if not url.startswith("data:image"):
                continue
            try:
                jpeg = base64.b64decode(url.split(",", 1)[1])
            except (IndexError, ValueError):
                continue
            rec = lookup(jpeg)
            if rec is not None:
                out.append((jpeg, rec))
                if len(out) >= limit:
                    return out
    return out
