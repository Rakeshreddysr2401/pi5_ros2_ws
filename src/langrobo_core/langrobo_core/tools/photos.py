"""Photo register — for any photo the robot took: when, from where, and the
camera stamp the Jetson holds its depth under.

"Go near it" after "what do you see?" means THE THING IN THAT PHOTO. The photo
is in the conversation (look() puts it there); what the conversation does not
carry is where the robot was and which depth frame belongs to it. This keeps
that, keyed by the JPEG itself, so approach.py can ask the vision model where
the object is IN THAT PHOTO and have the Jetson place it in the room with
that photo's depth and camera pose -- then drive there from wherever the robot
is now. No name matching (owner, 2026-09-27: "VLM needs to answer from the
frame it has and find the location").

Only the hash is kept, not the JPEG (the conversation has it). Bounded like
the Jetson's snapshot store (SNAPSHOTS = 24): an older photo's depth is gone
there, so it could not be placed anyway.
"""

import base64
import hashlib
import threading
from collections import OrderedDict

MAX_PHOTOS = 24

_lock = threading.Lock()
_by_hash: "OrderedDict[str, dict]" = OrderedDict()


def _key(jpeg: bytes) -> str:
    return hashlib.sha1(jpeg).hexdigest()


def record(jpeg: bytes, stamp, pose, when: float, epoch, source: str) -> None:
    """Remember a photo's provenance. No stamp -> nothing to ground against."""
    if not jpeg or not stamp:
        return
    with _lock:
        _by_hash[_key(jpeg)] = {"stamp": tuple(stamp), "pose": pose, "when": when,
                                "epoch": epoch, "source": source}
        _by_hash.move_to_end(_key(jpeg))
        while len(_by_hash) > MAX_PHOTOS:
            _by_hash.popitem(last=False)


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
