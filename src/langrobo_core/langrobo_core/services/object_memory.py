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

ACROSS POWER-OFF, IN THE HOUSE MAP (owner, 2026-11-16: "it needs to remember and go
the second time"; until then the rule was no memory across power-off). Every
entry carries the odom origin it was measured in (/fusion/status origin_epoch),
and -- when the Jetson has announced the saved house map (/home/room) and map ->
odom is known -- the same spot in that map: home = {"room", "map": [x, y]}. A
new origin (power cycle, pose restart) makes the odom numbers point at nothing;
an entry with a home in the SAME room is brought back into the new odom through
map -> odom (set_home: the caller says which room and where the map frame is),
exactly as saved places are (ros2_bridge add_known_location). An entry whose
home cannot be placed YET (map -> odom not there in the first seconds after
boot, or another room's map) is kept aside untouched -- never thrown away --
and comes back once it can. An entry with no home dies with its origin, as before.

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
import threading
import time
import uuid

SAME_OBJECT_M = 0.35       # a sighting this close to an entry that matches is that entry
MAX_ENTRIES = 800          # photo surveys (tools/survey.py) add ~5 per photo; the house, not one room
# remember/forget are read-modify-write on one file, and the background photo
# survey writes while tools read and write: one lock for all of it.
_lock = threading.RLock()
MAX_AGE_S = 14 * 86400.0   # older than this is not worth turning for (was 1 h until 2026-11-16:
                           # a bed or a TV does not move; anything that did is checked by a
                           # fresh look before the drive, and forgotten if gone)
FADE_S = 86400.0           # RViz: a sighting fades over a day (display only)

# Where the house map is, for entries kept across power-off (see the module text).
# provider() -> {"room": id, "map_in_odom": (x, y, yaw_deg)} (the map frame's pose in
# odom), or None while unknown. Set by the ROS bridge; None = odom-only, as before.
_home_provider = None


def set_home(provider) -> None:
    global _home_provider
    _home_provider = provider


def _home():
    try:
        h = _home_provider() if _home_provider else None
    except Exception:          # a display/TF hiccup must never break a turn
        return None
    if not h or not h.get("room") or not h.get("map_in_odom"):
        return None
    return h


def _map_to_odom(mx, my, t):
    tx, ty, tyaw = t
    c, s = math.cos(math.radians(tyaw)), math.sin(math.radians(tyaw))
    return tx + c * mx - s * my, ty + s * mx + c * my


def _odom_to_map(x, y, t):
    tx, ty, tyaw = t
    c, s = math.cos(math.radians(tyaw)), math.sin(math.radians(tyaw))
    dx, dy = x - tx, y - ty
    return c * dx + s * dy, -s * dx + c * dy

_STOP = {"the", "a", "an", "my", "your", "his", "her", "our", "their", "its",
         "that", "this", "those", "these", "some", "of", "on", "in", "at", "near",
         "to", "by", "with", "from", "and", "one", "thing", "object"}
_COLOURS = {"red", "orange", "yellow", "green", "blue", "purple", "violet", "pink",
            "brown", "black", "white", "grey", "gray", "silver", "gold", "golden",
            "beige", "cream", "maroon", "navy", "cyan", "transparent", "clear"}


def path() -> str:
    from .config import state_path
    return os.path.expanduser(os.environ.get("LANGROBO_OBJECT_MEMORY",
                                             state_path("object_memory.json")))


# Where-it-is phrases are context, not the object: "the white rectangular box
# ON THE MARBLE FLOOR" is a box. Everything from the first of these is cut.
_SPATIAL = {"on", "in", "at", "near", "under", "beside", "next", "behind", "by",
            "inside", "over", "above", "below", "against", "from", "beneath",
            "you", "i", "we", "that", "which", "seen", "saw"}


def _tokens(text: str) -> list:
    """The object part, as ordered words: filler dropped, plurals folded,
    cut at the first where-it-is / who-saw-it word ("... on the floor",
    "... you seen before")."""
    out = []
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if w in _SPATIAL and out:
            break
        if w in _STOP or w in _SPATIAL:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]                     # bottles -> bottle; glass stays glass
        out.append(w)
    return out


def _words(text: str) -> set:
    return set(_tokens(text))


def _head(tokens: list) -> str | None:
    """The object word: the last non-colour word ("white rectangular BOX")."""
    nouns = [w for w in tokens if w not in _COLOURS]
    return nouns[-1] if nouns else None


def match_score(query: str, description: str) -> float:
    """How well a remembered description answers a query, 0..1.

    Compared on the OBJECT PART of both (see _SPATIAL), and:
      * 0 if they name different colours ("red bottle" is not "orange bottle");
      * 0 if they name different things -- the head word, the last non-colour
        word, must agree ("white box" is not "white chair", "rectangular box"
        is not "rectangular table"); a query that is only a colour ("the
        white one") is decided by colour;
      * else the share of the SHORTER one's words the other has.
    The shorter side, not the query: the model rewrites "go near it" as "the
    white rectangular box on the marble floor", and the survey stored "white
    box" -- 2 of 5 query words, which missed a box the robot had just
    photographed (floor test, 2026-09-27)."""
    q, d = _tokens(query), _tokens(description)
    if not q or not d:
        return 0.0
    qs, ds = set(q), set(d)
    qc, dc = qs & _COLOURS, ds & _COLOURS
    if qc and dc and not (qc & dc):
        return 0.0
    qh, dh = _head(q), _head(d)
    if qh and dh and qh != dh:
        return 0.0
    if qh and not dh:
        return 0.0                         # asked for a thing, remembered only a colour
    return len(qs & ds) / min(len(qs), len(ds))


def _load(epoch) -> list:
    return _load_all(epoch)[0]


def _load_all(epoch) -> tuple:
    """(live, aside): live = usable now, in THIS origin's odom; aside = entries from
    another origin whose house-map spot cannot be placed now (kept, written back)."""
    try:
        with open(path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    aside = list(data.get("aside", []))
    entries = list(data.get("objects", []))
    if epoch is not None and data.get("epoch") is not None and data["epoch"] != epoch:
        aside = aside + entries            # another odom origin: these odom numbers mean nothing
        entries = []
    if aside:
        home = _home()
        keep = []
        for e in aside:
            h = e.get("home") or {}
            if home and h.get("room") == home["room"] and h.get("map"):
                x, y = _map_to_odom(h["map"][0], h["map"][1], home["map_in_odom"])
                e = {**e, "x": round(x, 3), "y": round(y, 3), "seen_from": None, "via_home": True}
                entries.append(e)
            elif h.get("map"):
                keep.append(e)             # placeable later (map -> odom not yet, other room)
        aside = keep
    # Files written before ids were unique hold one id per PHOTO (see
    # _remember): give each repeat its own, so forget() drops one object.
    # Deterministic (id-2, id-3, ...), so the same file gives the same ids to
    # a recall() and the forget() after it; the next save persists them.
    seen = set()
    for e in entries:
        base, n = e.get("id"), 1
        while e.get("id") in seen:
            n += 1
            e["id"] = f"{base}-{n}"
        seen.add(e.get("id"))
    return entries, aside


def _with_home(e: dict) -> dict:
    """Stamp an entry with its spot in the house map, when that is known now."""
    home = _home()
    if home:
        mx, my = _odom_to_map(e["x"], e["y"], home["map_in_odom"])
        e["home"] = {"room": home["room"], "map": [round(mx, 3), round(my, 3)]}
    return e


def _save(entries: list, epoch, aside: list | None = None) -> None:
    if aside is None:
        aside = _load_all(epoch)[1]        # never drop what is kept aside
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".object_memory.")
    with os.fdopen(fd, "w") as f:
        json.dump({"epoch": epoch, "objects": entries[-MAX_ENTRIES:], "aside": aside[-MAX_ENTRIES:]},
                  f, indent=1)
    os.replace(tmp, p)


def remember(description: str, x: float, y: float, seen_from, epoch,
             when: float | None = None, **meta) -> dict:
    with _lock:
        return _remember(description, x, y, seen_from, epoch, when, **meta)


def _remember(description: str, x: float, y: float, seen_from, epoch,
              when: float | None = None, **meta) -> dict:
    """Record a sighting. A matching entry within SAME_OBJECT_M is updated in
    place (it is the same object); otherwise a new entry is added.
    seen_from: (x, y, yaw_deg) of the robot, or None."""
    when = time.time() if when is None else when
    entries, aside = _load_all(epoch)
    for e in entries:
        if (max(match_score(description, e["description"]), match_score(e["description"], description)) >= 0.5
                and math.hypot(e["x"] - x, e["y"] - y) <= SAME_OBJECT_M):
            e.update(description=description, x=round(x, 3), y=round(y, 3), seen_at=when,
                     seen_from=list(seen_from) if seen_from else None,
                     sightings=e.get("sightings", 1) + 1, **meta)
            e.pop("via_home", None)         # measured again in this origin's odom
            _with_home(e)
            entries.remove(e)
            entries.append(e)               # most recent last
            _save(entries, epoch, aside)
            return e
    # Unique, not time-derived: the photo survey stores every object in one
    # photo with that photo's `when`, and an id built from it gave them all the
    # same id -- forget() of one then dropped the whole photo's objects, and
    # approach's "has it moved?" check compared an entry with itself.
    e = {"id": uuid.uuid4().hex[:12], "description": description,
         "x": round(x, 3), "y": round(y, 3), "seen_at": when,
         "seen_from": list(seen_from) if seen_from else None, "sightings": 1, **meta}
    _with_home(e)
    entries.append(e)
    _save(entries, epoch, aside)
    return e


def recall(query: str, epoch, now: float | None = None, max_age_s: float = MAX_AGE_S) -> list:
    """Entries answering `query` ("" = all), best match first, then most
    recent; none older than max_age_s."""
    now = time.time() if now is None else now
    out = []
    for e in _load(epoch):
        if now - e.get("seen_at", 0) > max_age_s and not e.get("pinned"):
            continue                       # pinned = the user said "remember the X": never ages out
        s = 1.0 if not query.strip() else match_score(query, e["description"])
        if s >= 0.5:
            out.append((s, e.get("seen_at", 0), e))
    out.sort(key=lambda t: (-t[0], -t[1]))
    return [e for _, _, e in out]


def forget(entry_id: str, epoch) -> bool:
    with _lock:
        return _forget(entry_id, epoch)


def _forget(entry_id: str, epoch) -> bool:
    entries, aside = _load_all(epoch)
    kept = [e for e in entries if e.get("id") != entry_id]
    if len(kept) == len(entries):
        return False
    _save(kept, epoch, aside)
    return True


def labels(epoch, now: float | None = None, max_age_s: float = MAX_AGE_S) -> dict:
    """{description: [entries, most recent first]} of everything remembered now --
    what a by-meaning lookup (tools/memory_lookup.py) chooses from."""
    now = time.time() if now is None else now
    out: dict = {}
    for e in sorted(_load(epoch), key=lambda e: -e.get("seen_at", 0)):
        if now - e.get("seen_at", 0) <= max_age_s or e.get("pinned"):
            out.setdefault(e["description"], []).append(e)
    return out


def colours_clash(a: str, b: str) -> bool:
    """Both name a colour and none is shared: "red bottle" is not "orange bottle"."""
    ca, cb = set(_tokens(a)) & _COLOURS, set(_tokens(b)) & _COLOURS
    return bool(ca and cb and not (ca & cb))


def object_part(text: str) -> str:
    """The object words only ("the white box on the floor" -> "white box")."""
    return " ".join(_tokens(text))


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
