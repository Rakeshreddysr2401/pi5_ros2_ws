"""object_memory — where the robot has seen things, this session.

Rover repo INTELLIGENCE_PLAN.md B3, 2026-09-26. The owner's case: the robot
photographs a bottle at position 1; later, at position 2, "go near the
bottle" should not start a 4-view VLM search from scratch -- it should know
where the bottle was, turn to face that spot from where it is NOW, check the
bottle is still there (someone may have moved it), and go.

WHAT IS STORED. The object's position in odom -- the Jetson's pixel_to_goal
"object", grounded at the moment of the photo (B2) on the VLM's box -- plus
when, and the pose it was seen from. Positions in odom stay valid while the
pose does not drift: fusion2 holds it to <1 cm (rover repo LOCALIZATION.md),
and three locates of one bottle from three poses agreed within 0.7 cm
(INTELLIGENCE_PLAN.md §5).

ONE SESSION. Every entry carries the odom origin it was measured in
(/fusion/status origin_epoch). A different origin -- a power cycle, a pose
layer restart -- means those numbers point at nothing, so the file is emptied
rather than served. That is also the owner's rule: no memory across power-off.

A HINT, NEVER A FACT. Things move. Callers confirm with a fresh look before
driving at a remembered position (tools/approach.py), and forget an entry the
look does not confirm.

FILE, NOT PROCESS MEMORY. agent_node (Telegram, voice) and LangGraph Studio
are two processes running the same graph; a file lets something located in
one be recalled in the other. Small, rewritten whole, atomically.

Pure: no ROS, no LLM. The caller supplies the epoch and the poses.
"""

import json
import math
import os
import re
import tempfile
import time

SAME_OBJECT_M = 0.35       # a sighting this close to an entry that matches is that entry
MAX_ENTRIES = 50
MAX_AGE_S = 3600.0         # older than this is not worth turning for

_STOP = {"the", "a", "an", "my", "your", "his", "her", "our", "their", "its",
         "that", "this", "those", "these", "some", "of", "on", "in", "at", "near",
         "to", "by", "with", "from", "and", "one", "thing", "object"}
_COLOURS = {"red", "orange", "yellow", "green", "blue", "purple", "violet", "pink",
            "brown", "black", "white", "grey", "gray", "silver", "gold", "golden",
            "beige", "cream", "maroon", "navy", "cyan", "transparent", "clear"}


def path() -> str:
    return os.path.expanduser(os.environ.get("LANGROBO_OBJECT_MEMORY",
                                             "~/.langrobo/object_memory.json"))


def _words(text: str) -> set:
    out = set()
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if w in _STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]                     # bottles -> bottle; glass stays glass
        out.add(w)
    return out


def match_score(query: str, description: str) -> float:
    """How well a remembered description answers a query, 0..1.

    The share of the query's words the description has, and 0 if the two name
    different colours ("the red bottle" is not "the orange bottle"). Plain word
    overlap on purpose: the descriptions come from the user and the model in
    ordinary words ("the orange bottle", "bottle on the floor")."""
    q, d = _words(query), _words(description)
    if not q or not d:
        return 0.0
    qc, dc = q & _COLOURS, d & _COLOURS
    if qc and dc and not (qc & dc):
        return 0.0
    return len(q & d) / len(q)


def _load(epoch) -> list:
    try:
        with open(path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    if epoch is not None and data.get("epoch") is not None and data["epoch"] != epoch:
        return []                          # another odom origin: these point at nothing
    return list(data.get("objects", []))


def _save(entries: list, epoch) -> None:
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".object_memory.")
    with os.fdopen(fd, "w") as f:
        json.dump({"epoch": epoch, "objects": entries[-MAX_ENTRIES:]}, f, indent=1)
    os.replace(tmp, p)


def remember(description: str, x: float, y: float, seen_from, epoch,
             when: float | None = None, **meta) -> dict:
    """Record a sighting. A matching entry within SAME_OBJECT_M is updated in
    place (it is the same object); otherwise a new entry is added.
    seen_from: (x, y, yaw_deg) of the robot, or None."""
    when = time.time() if when is None else when
    entries = _load(epoch)
    for e in entries:
        if (max(match_score(description, e["description"]), match_score(e["description"], description)) >= 0.5
                and math.hypot(e["x"] - x, e["y"] - y) <= SAME_OBJECT_M):
            e.update(description=description, x=round(x, 3), y=round(y, 3), seen_at=when,
                     seen_from=list(seen_from) if seen_from else None,
                     sightings=e.get("sightings", 1) + 1, **meta)
            entries.remove(e)
            entries.append(e)               # most recent last
            _save(entries, epoch)
            return e
    e = {"id": f"{int(when * 1000) % 10**9:09d}", "description": description,
         "x": round(x, 3), "y": round(y, 3), "seen_at": when,
         "seen_from": list(seen_from) if seen_from else None, "sightings": 1, **meta}
    entries.append(e)
    _save(entries, epoch)
    return e


def recall(query: str, epoch, now: float | None = None, max_age_s: float = MAX_AGE_S) -> list:
    """Entries answering `query` ("" = all), best match first, then most
    recent; none older than max_age_s."""
    now = time.time() if now is None else now
    out = []
    for e in _load(epoch):
        if now - e.get("seen_at", 0) > max_age_s:
            continue
        s = 1.0 if not query.strip() else match_score(query, e["description"])
        if s >= 0.5:
            out.append((s, e.get("seen_at", 0), e))
    out.sort(key=lambda t: (-t[0], -t[1]))
    return [e for _, _, e in out]


def forget(entry_id: str, epoch) -> bool:
    entries = _load(epoch)
    kept = [e for e in entries if e.get("id") != entry_id]
    if len(kept) == len(entries):
        return False
    _save(kept, epoch)
    return True


def relative(entry: dict, pose) -> tuple | None:
    """(distance_m, bearing_deg) of an entry from pose (x, y, yaw_deg);
    bearing 0 = straight ahead, + = to the left. None without a pose."""
    if not pose:
        return None
    dx, dy = entry["x"] - pose[0], entry["y"] - pose[1]
    bearing = (math.degrees(math.atan2(dy, dx)) - pose[2] + 180.0) % 360.0 - 180.0
    return math.hypot(dx, dy), bearing


def describe_age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} h {s % 3600 // 60} min ago"
