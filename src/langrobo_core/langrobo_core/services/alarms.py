"""Timers, alarms and reminders -- a small file the tools write and agent_node
rings. Pure zone (no rclpy).

Owner, 2026-10-04: "fill the gaps -- be super good, like Siri and Alexa".
The everyday ones: "set a timer for 10 minutes", "wake me up at 6",
"remind me at 7 to take my medicine", "what timers do I have?", "cancel the
tea timer". (Reminders were cut on 2026-09-06 as real weight; this is the lean
version: one JSON file, no scheduler service, no database.)

Who writes, who rings: the tools add and cancel (they run in agent_node AND in
Studio's process); only agent_node pops what is due and speaks it, so an item
never rings twice. The file is locked for every read-modify-write and
replaced atomically, so the two processes cannot corrupt it.

Survives restarts: an item that fell due while the brain was down rings once
when it comes back (if it is less than LATE_LIMIT_S late), then is dropped.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

KINDS = ("timer", "alarm", "reminder")
LATE_LIMIT_S = 6 * 3600          # older than this when the brain comes back: drop, don't ring
DAY_S = 24 * 3600


def _path() -> Path:
    base = os.environ.get("LANGROBO_STATE_DIR") or str(Path.home() / ".local/state/langrobo")
    return Path(base) / "alarms.json"


@contextmanager
def _locked():
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                items = json.loads(p.read_text())
                if not isinstance(items, list):
                    items = []
            except (OSError, ValueError):
                items = []
            box = {"items": items}
            yield box
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(box["items"], indent=1))
            os.replace(tmp, p)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


# ── when ─────────────────────────────────────────────────────────────────────

_AT = re.compile(r"^\s*(?:(\d{4}-\d{2}-\d{2})[ T])?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$", re.I)


def parse_due(minutes: float = 0.0, at: str = "", now: float | None = None) -> float:
    """Epoch seconds an item is due.

    minutes > 0: that long from now ("in 10 minutes", "for 90 seconds" = 1.5).
    at: "18:30", "6:30 pm", "7 am", "2026-10-05 07:30" -- local time; a time
    of day that has already passed today means tomorrow.
    Raises ValueError with a sentence the model can say back."""
    now = time.time() if now is None else now
    if minutes and minutes > 0:
        return now + float(minutes) * 60.0
    m = _AT.match(at or "")
    if not m:
        raise ValueError("I need a time: minutes from now, or a time like 18:30")
    date_s, hh, mm, ampm = m.groups()
    h, mi = int(hh), int(mm or 0)
    if ampm:
        pm = ampm.lower().startswith("p")
        if not 1 <= h <= 12:
            raise ValueError(f"{at!r} is not a valid time")
        h = (h % 12) + (12 if pm else 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"{at!r} is not a valid time")
    base = datetime.fromtimestamp(now)
    if date_s:
        day = datetime.strptime(date_s, "%Y-%m-%d")
        due = day.replace(hour=h, minute=mi, second=0, microsecond=0)
        if due.timestamp() <= now:
            raise ValueError(f"{at} is in the past")
        return due.timestamp()
    due = base.replace(hour=h, minute=mi, second=0, microsecond=0)
    if due.timestamp() <= now:
        due += timedelta(days=1)
    return due.timestamp()


def spoken_when(due: float, now: float | None = None) -> str:
    """"in 9 minutes", "at 6:30 AM tomorrow" -- how a person would say it."""
    now = time.time() if now is None else now
    left = due - now
    if left < 3600 - 30:
        mins = max(1, round(left / 60))
        if left < 90:
            return f"in {max(1, round(left))} seconds"
        return f"in {mins} minute{'s' if mins != 1 else ''}"
    d, n = datetime.fromtimestamp(due), datetime.fromtimestamp(now)
    clock = d.strftime("%I:%M %p").lstrip("0")
    if d.date() == n.date():
        return f"at {clock}"
    if d.date() == (n + timedelta(days=1)).date():
        return f"at {clock} tomorrow"
    return f"at {clock} on {d.strftime('%A')}"


# ── the list ─────────────────────────────────────────────────────────────────

def add(kind: str, due: float, label: str = "", daily: bool = False,
        now: float | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    item = {"id": uuid.uuid4().hex[:6], "kind": kind, "due": float(due),
            "label": (label or "").strip(), "daily": bool(daily),
            "created": time.time() if now is None else now}
    with _locked() as box:
        box["items"].append(item)
    return item


def pending(now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    with _locked() as box:
        return sorted((i for i in box["items"] if i["due"] > now - LATE_LIMIT_S),
                      key=lambda i: i["due"])


def _matches(item: dict, which: str) -> bool:
    w = (which or "").strip().lower()
    if w in ("", "all", "everything"):
        return True
    if w in ("timer", "timers", "alarm", "alarms", "reminder", "reminders"):
        return item["kind"] == w.rstrip("s")
    words = {x for x in re.findall(r"[a-z0-9]+", w) if x not in {"the", "my", "a", "timer", "alarm", "reminder"}}
    hay = f"{item['label']} {item['kind']}".lower()
    return bool(words) and all(x in hay for x in words)


def cancel(which: str = "", now: float | None = None) -> list[dict]:
    """Remove what `which` names ("all", "timers", "tea", "the 6 am alarm"
    by label words). Returns what was removed."""
    with _locked() as box:
        gone = [i for i in box["items"] if _matches(i, which)]
        box["items"] = [i for i in box["items"] if i not in gone]
    return gone


def pop_due(now: float | None = None) -> list[dict]:
    """Everything due now, for agent_node to ring. Daily items move to the
    same time tomorrow; the rest are removed. Items too late are dropped silently."""
    now = time.time() if now is None else now
    out = []
    with _locked() as box:
        keep = []
        for i in box["items"]:
            if i["due"] > now:
                keep.append(i)
                continue
            if now - i["due"] <= LATE_LIMIT_S:
                out.append(dict(i))
            if i.get("daily"):
                nxt = dict(i)
                while nxt["due"] <= now:
                    nxt["due"] += DAY_S
                keep.append(nxt)
        box["items"] = keep
    return out


def announcement(item: dict, now: float | None = None) -> str:
    """What the robot says when an item rings (English; Sarvam speaks it in Telugu)."""
    now = time.time() if now is None else now
    label = item.get("label") or ""
    late = now - item["due"] > 120
    if item["kind"] == "timer":
        text = f"Your {label} timer is done." if label else "Your timer is done."
    elif item["kind"] == "alarm":
        clock = datetime.fromtimestamp(item["due"]).strftime("%I:%M %p").lstrip("0")
        text = f"It's {clock}. " + (label[:1].upper() + label[1:] + "." if label else "Time to wake up.")
    else:
        text = f"Reminder: {label}." if label else "This is your reminder."
    if late:
        text += " Sorry, I was offline when it was due."
    return text


def describe(item: dict, now: float | None = None) -> str:
    label = f" for {item['label']}" if item.get("label") and item["kind"] != "reminder" else ""
    what = f"reminder to {item['label']}" if item["kind"] == "reminder" and item.get("label") else item["kind"] + label
    return f"{what}, {spoken_when(item['due'], now)}" + (", every day" if item.get("daily") else "")
