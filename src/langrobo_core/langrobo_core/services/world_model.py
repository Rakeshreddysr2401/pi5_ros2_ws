"""The robot's memory of WHERE THINGS ARE — persistent, multi-instance, pure zone.

Why this exists
---------------
The depth pipeline (Jetson `detections_3d`) streams object positions in the
brain's navigation frame (ROS2Bridge.NAV_FRAME — `odom` on this rover),
and the bridge used to cache them in a bare `dict[label] -> position` that
lived only in RAM. Three consequences, all of them things the robot is
explicitly supposed to do (New_Requirement.md goals 1-3):

* **Restart amnesia.** Saved *locations* persisted to disk; seen *objects* did
  not. `systemctl restart langrobo-brain` and "go near the chair" answered "I
  haven't seen one before either" about a chair it had been staring at all day.
* **One position per label.** A second chair overwrote the first, so "go near
  the chair" drove to whichever chair the detector happened to publish last —
  not the nearest one. With four dining chairs that is wrong three times out
  of four.
* **No way to ask.** Nothing but the approach tools could read the map, so the
  robot could DRIVE to the chair but could not TELL you where the chair was.

This class owns all three. It is deliberately in the pure zone: no rclpy, no
bridge, unit-testable with a fake clock, and shared by the ROS bridge and any
future consumer (a room-level summary for the briefing, a "what changed?"
diff, self-learning).

Time
----
Observation stamps are WALL clock (`time.time()`), not monotonic, because they
have to survive a restart. That is safe here: every stamp is written when the
message is RECEIVED on this machine, so the Pi5<->Jetson clock drift that
monotonic protects against (CLAUDE.md gotcha) never enters the comparison. A
system clock step only skews reported ages, never positions.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time

logger = logging.getLogger(__name__)

# Two detections of the same label closer than this are the same physical
# object seen again, not a second one. Roughly a chair's width: tight enough to
# tell two dining chairs apart, loose enough to absorb depth noise and vSLAM
# drift on repeat sightings of one object.
DEFAULT_MERGE_RADIUS_M = 0.6

# Per label. A living room does not have fifty distinct chairs; a detector
# having a bad day can invent them. The cap bounds both the file and the
# nearest() scan, evicting the least-recently-seen first.
DEFAULT_MAX_INSTANCES = 8

# The map is written at most this often, and only when something moved —
# detections arrive at several Hz and this robot boots off an SD card.
DEFAULT_SAVE_INTERVAL_S = 30.0

# Older than this and the robot is REMEMBERING, not seeing. Lives here because
# both the approach tools and where_is need it and the two must never disagree
# — that disagreement is how a robot ends up saying "I can see it" about
# something that left the room.
LIVE_DETECTION_S = 3.0


def _humanise_age(seconds: float) -> str:
    """Age as a person would say it out loud (this text reaches TTS)."""
    if seconds < 45:
        return "just now"
    minutes = seconds / 60.0
    if minutes < 1.5:
        return "about a minute ago"
    if minutes < 55:
        return f"about {int(round(minutes))} minutes ago"
    hours = minutes / 60.0
    if hours < 1.5:
        return "about an hour ago"
    if hours < 22:
        return f"about {int(round(hours))} hours ago"
    days = hours / 24.0
    if days < 1.5:
        return "about a day ago"
    return f"about {int(round(days))} days ago"


class WorldModel:
    """Map-frame positions of everything the robot has seen, by label.

    Thread-safe: the ROS executor thread writes (`observe`) while the graph
    worker thread reads (`nearest`, `freshest`, `summary`).
    """

    def __init__(self, path: str | None = None,
                 merge_radius_m: float = DEFAULT_MERGE_RADIUS_M,
                 max_instances: int = DEFAULT_MAX_INSTANCES,
                 save_interval_s: float = DEFAULT_SAVE_INTERVAL_S,
                 clock=time.time):
        self._path = os.path.expanduser(path) if path else None
        self._merge_radius = merge_radius_m
        self._max_instances = max_instances
        self._save_interval = save_interval_s
        self._clock = clock
        self._lock = threading.RLock()
        self._objects: dict[str, list[dict]] = {}
        self._dirty = False
        self._last_save = 0.0
        if self._path:
            self.load()

    # ── Writing ───────────────────────────────────────────────────────────

    def observe(self, label: str, x: float, y: float, z: float = 0.0,
                conf: float = 0.0) -> None:
        """Record a NAV_FRAME sighting, merging into a nearby instance if there
        is one. Cheap enough to call at detector rate."""
        label = str(label).lower().strip()
        if not label:
            return
        now = self._clock()
        with self._lock:
            instances = self._objects.setdefault(label, [])
            match = self._closest_within(instances, x, y, self._merge_radius)
            if match is not None:
                match.update(x=x, y=y, z=z, conf=conf, at=now,
                             seen_count=match.get("seen_count", 1) + 1)
            else:
                instances.append({"x": x, "y": y, "z": z, "conf": conf,
                                  "at": now, "first_seen": now, "seen_count": 1})
                if len(instances) > self._max_instances:
                    # Evict the one we have not seen for longest — an object
                    # that moved leaves a ghost, and the ghost is never
                    # re-observed, so it ages out on its own.
                    instances.sort(key=lambda i: i["at"])
                    del instances[0]
            self._dirty = True
        self._maybe_save(now)

    def forget(self, label: str) -> int:
        """Drop everything known about `label`. Returns how many instances went.

        The honest answer to "no, the chair isn't there any more" — better than
        letting the robot drive to a ghost until the eviction cap catches up.
        """
        label = str(label).lower().strip()
        with self._lock:
            gone = len(self._objects.pop(label, []))
            if gone:
                self._dirty = True
        if gone:
            self.save()
        return gone

    # ── Reading ───────────────────────────────────────────────────────────

    def freshest(self, label: str, max_age_s: float | None = None) -> dict | None:
        """Most recently seen instance of `label`, or None."""
        with self._lock:
            best = max(self._objects.get(self._key(label), []),
                       key=lambda i: i["at"], default=None)
            return self._view(best, max_age_s)

    def nearest(self, label: str, rx: float, ry: float,
                max_age_s: float | None = None) -> dict | None:
        """Instance of `label` closest to the robot at (rx, ry), or None.

        This is what "go near the chair" should use: with several chairs in the
        map, the one you mean is almost always the one you can walk to.
        """
        now = self._clock()
        with self._lock:
            candidates = [
                i for i in self._objects.get(self._key(label), [])
                if max_age_s is None or now - i["at"] <= max_age_s
            ]
            if not candidates:
                return None
            best = min(candidates, key=lambda i: math.hypot(i["x"] - rx, i["y"] - ry))
            view = self._view(best, None)
            view["distance_m"] = math.hypot(best["x"] - rx, best["y"] - ry)
            return view

    def all_fresh(self, max_age_s: float) -> dict[str, dict]:
        """{label: freshest instance} for everything seen within max_age_s."""
        with self._lock:
            out = {}
            for label in self._objects:
                view = self.freshest(label, max_age_s)
                if view is not None:
                    out[label] = view
            return out

    def labels(self) -> list[str]:
        with self._lock:
            return sorted(label for label, inst in self._objects.items() if inst)

    def summary(self, label: str) -> dict | None:
        """Everything known about one label: instance count, freshest sighting."""
        with self._lock:
            instances = self._objects.get(self._key(label), [])
            if not instances:
                return None
            freshest = max(instances, key=lambda i: i["at"])
            return {"label": self._key(label), "count": len(instances),
                    **self._view(freshest, None)}

    # ── Persistence ───────────────────────────────────────────────────────

    def load(self) -> None:
        if not self._path:
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning("World model unreadable (%s) — starting empty", e)
            return
        objects = data.get("objects", {})
        if not isinstance(objects, dict):
            return
        with self._lock:
            for label, instances in objects.items():
                clean = [i for i in instances if self._valid(i)]
                if clean:
                    self._objects[str(label).lower()] = clean[:self._max_instances]
        logger.info("World model: loaded %d labels from %s",
                    len(self._objects), self._path)

    def save(self) -> None:
        """Atomic write — a half-written map read at the next boot is worse
        than no map, and this file is written on a robot that gets its power
        cut rather than shut down."""
        if not self._path:
            return
        with self._lock:
            payload = {"version": 1, "saved_at": self._clock(),
                       "objects": {k: v for k, v in self._objects.items() if v}}
            self._dirty = False
            self._last_save = self._clock()
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, self._path)
        except Exception as e:
            logger.warning("Could not save world model: %s", e)

    def _maybe_save(self, now: float) -> None:
        if not self._path or not self._dirty:
            return
        if now - self._last_save < self._save_interval:
            return
        self.save()

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _key(label: str) -> str:
        return str(label).lower().strip()

    @staticmethod
    def _valid(inst) -> bool:
        return (isinstance(inst, dict)
                and all(isinstance(inst.get(k), (int, float))
                        for k in ("x", "y", "at")))

    @staticmethod
    def _closest_within(instances: list[dict], x: float, y: float,
                        radius: float) -> dict | None:
        best, best_d = None, radius
        for inst in instances:
            d = math.hypot(inst["x"] - x, inst["y"] - y)
            if d <= best_d:
                best, best_d = inst, d
        return best

    def _view(self, inst: dict | None, max_age_s: float | None) -> dict | None:
        """Public copy of an instance with a derived, never-negative age."""
        if inst is None:
            return None
        age = max(0.0, self._clock() - inst["at"])
        if max_age_s is not None and age > max_age_s:
            return None
        return {"x": inst["x"], "y": inst["y"], "z": inst.get("z", 0.0),
                "conf": inst.get("conf", 0.0), "age_s": age,
                "seen_count": inst.get("seen_count", 1),
                "age_phrase": _humanise_age(age)}


# ── Module singleton ─────────────────────────────────────────────────────────
# Same shape as services/telegram.py, watch.py and memory.py: agent_node calls
# init() once at startup with the validated config, everything else calls
# get(). This is what makes the world model a first-class service rather than
# a field on the ROS bridge — every other file under ~/.langrobo/ is owned by
# a service, and `locations.json` being the lone bridge-owned exception is
# precisely why object positions were never persisted in the first place.
#
# One deliberate difference from its siblings: get() never returns None. "No
# Telegram configured" is a real state a tool must speak about; "no world
# model" is not — an unmapped robot just has an empty map. So an uninitialised
# get() yields a memory-only instance, and tools stay free of None-checks.

_instance: WorldModel | None = None


def init(cfg) -> WorldModel:
    """Install the world model from WorldModelConfig. Called once at startup."""
    global _instance
    _instance = WorldModel(
        path=cfg.state_path if getattr(cfg, "enabled", True) else None,
        merge_radius_m=cfg.merge_radius_m,
        max_instances=cfg.max_instances,
        save_interval_s=cfg.save_interval_s,
    )
    logger.info("World model: %s (%d labels)",
                _instance._path or "memory-only", len(_instance.labels()))
    return _instance


def get() -> WorldModel:
    """The live world model — never None (see the note above)."""
    global _instance
    if _instance is None:
        _instance = WorldModel(path=None)
    return _instance


def reset(instance: WorldModel | None = None) -> WorldModel:
    """Replace the singleton — tests, and Studio's stub startup."""
    global _instance
    _instance = instance if instance is not None else WorldModel(path=None)
    return _instance
