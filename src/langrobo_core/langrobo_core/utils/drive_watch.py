"""Watching a background drive: say when it struggles, stop when it is stuck. Pure zone.

Telegram, 2026-10-29: "go near the black bag". reach could not find a way (the
widest gap was 55 cm, nav2 needs 56): it tried tight routes, backed out, passed,
waited -- 7 attempts, 305 s, the rover wandered 1.8 m -- and only then did the
asker hear anything: "I couldn't get near the black bag: no path". Five minutes
of silence from a robot moving about the room.

reach (the Jetson) gives up only at 300 s, and other callers (explore, RViz
goals) want that patience, so the brain decides for ITS drives, from the
attempt lines reach already publishes on /reach/status:

  attempt 2 starts, or an
  attempt failed and 40 s     -> one progress line to whoever asked
  attempt >= GIVE_UP_ATTEMPTS
  and >= GIVE_UP_S running    -> stop: cancel reach, report where it stopped

ROS2Bridge._reach_worker calls DriveWatch.step() every half second. Nothing
here touches ROS.
"""

from __future__ import annotations

import math
import os
import re

GIVE_UP_S = float(os.environ.get("LANGROBO_DRIVE_GIVE_UP_S", "90"))
GIVE_UP_ATTEMPTS = int(os.environ.get("LANGROBO_DRIVE_GIVE_UP_ATTEMPTS", "3"))
PROGRESS_ATTEMPT = 2      # a progress line when the 2nd attempt starts ...
PROGRESS_S = 40.0         # ... or when one has failed and the drive is this old


def plain_why(why: str) -> str:
    """reach's reason, as a person would say it. Unknown reasons pass as they are."""
    w = (why or "").strip()
    low = w.lower()
    if "widest way" in low or "narrow" in low or "gap" in low:
        return "the way there is too narrow for me"
    if low.startswith("no path"):
        return "I can't find a way through"
    if "refused" in low and ("something" in low or "obstacle" in low):
        return "something is in my way"
    if "stalled" in low or "stuck" in low:
        return "I got stuck"
    return re.sub(r"\s+", " ", w) or "I can't get through"


def _place(label: str) -> str:
    label = (label or "").replace("_", " ").strip()
    if not label:
        return "there"
    if label.startswith(("near ", "to ")):
        return label
    return label if label.startswith("the ") else f"to the {label}"


class DriveWatch:
    """One background drive. step() -> None, ("progress", text) once, or
    ("give_up", why) once; why is a sentence tail for the failure report."""

    def __init__(self, label: str, goal_xy: tuple | None = None):
        self.label = label
        self.goal_xy = goal_xy
        self.progress_sent = False
        self.done = False

    def step(self, lines: list, elapsed_s: float, here_xy=None):
        """here_xy: (x, y), or a function returning it (asked only on giving up)."""
        if self.done or any("result" in d for d in lines):
            return None
        attempts = max((int(d.get("attempt") or 0) for d in lines), default=0)
        # Only a "recover" line says why an attempt failed; move lines carry a
        # "why" too ("goal_exec reached: 0.6 cm"), which is no reason at all.
        whys = [d["why"] for d in lines if d.get("phase") == "recover" and d.get("why")]
        why = plain_why(self._telling(whys))
        if attempts >= GIVE_UP_ATTEMPTS and elapsed_s >= GIVE_UP_S:
            self.done = True
            here = here_xy() if callable(here_xy) else here_xy
            return ("give_up", self._give_up_text(why, attempts, elapsed_s, here))
        struggling = attempts >= PROGRESS_ATTEMPT or (whys and elapsed_s >= PROGRESS_S)
        if not self.progress_sent and struggling:
            self.progress_sent = True
            return ("progress", f"Still trying to get {_place(self.label)}: {why}. "
                                f"I'm trying another way.")
        return None

    @staticmethod
    def _telling(whys: list) -> str:
        """The most informative reason so far: a narrow way beats a bare 'no path'."""
        for w in reversed(whys):
            if "widest way" in w or "narrow" in w:
                return w
        return whys[-1] if whys else ""

    def _give_up_text(self, why: str, attempts: int, elapsed_s: float, here_xy) -> str:
        where = ""
        if here_xy is not None and self.goal_xy is not None:
            d = math.hypot(self.goal_xy[0] - here_xy[0], self.goal_xy[1] - here_xy[1])
            where = f", about {d:.1f} m short of it"
        # Never the word "cancelled": nav_report drops a cancelled drive's report.
        return (f"{why}. After {attempts} tries in {int(elapsed_s)} s I've stopped{where}. "
                f"Move something out of the way or ask me again and I'll try once more")
