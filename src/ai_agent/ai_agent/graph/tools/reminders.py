"""Reminders & timers — pure zone (no rclpy).

The first self-initiated speech path: chat sets reminders with these tools,
agent_node polls the store on a ROS timer and injects a "[SYSTEM] Reminder due"
turn when one fires, and the supervisor routes it to chat to announce out loud.

Persistence: one JSON file (default ~/.langrobo/reminders.json, override with
LANGROBO_REMINDERS_FILE) so reminders survive brain restarts. Timers are just
short reminders — same store, same announcement path.
"""

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Optional

from langchain_core.tools import tool

_DEFAULT_PATH = "~/.langrobo/reminders.json"


@dataclass
class Reminder:
    id: int
    text: str
    due: float       # epoch seconds
    created: float


def _fmt_due(due: float) -> str:
    """Human/speech-friendly due time: '7:42 PM', plus 'tomorrow' if not today."""
    dt, now = datetime.fromtimestamp(due), datetime.now()
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == now.date():
        return clock
    if dt.date() == (now + timedelta(days=1)).date():
        return f"{clock} tomorrow"
    return f"{clock} on {dt.strftime('%A, %B %d')}"


class ReminderStore:
    """Thread-safe, JSON-persisted reminder list (worker thread + ROS timer)."""

    def __init__(self, path: str | None = None):
        self._path = os.path.expanduser(
            path or os.getenv("LANGROBO_REMINDERS_FILE", _DEFAULT_PATH))
        self._lock = threading.Lock()
        self._next_id = 1
        self._reminders: list[Reminder] = []
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path) as f:
                data = json.load(f)
            self._next_id = data.get("next_id", 1)
            self._reminders = [Reminder(**r) for r in data.get("reminders", [])]
        except (OSError, ValueError, TypeError):
            pass  # missing or corrupt file — start empty

    def _save(self) -> None:
        # Called with the lock held. Write-then-rename so a crash mid-write
        # never corrupts the store.
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"next_id": self._next_id,
                       "reminders": [asdict(r) for r in self._reminders]}, f, indent=1)
        os.replace(tmp, self._path)

    def add(self, text: str, due: float) -> Reminder:
        with self._lock:
            r = Reminder(id=self._next_id, text=text, due=due, created=time.time())
            self._next_id += 1
            self._reminders.append(r)
            self._save()
            return r

    def active(self) -> list[Reminder]:
        with self._lock:
            return sorted(self._reminders, key=lambda r: r.due)

    def cancel(self, reminder_id: int) -> Optional[Reminder]:
        with self._lock:
            for r in self._reminders:
                if r.id == reminder_id:
                    self._reminders.remove(r)
                    self._save()
                    return r
            return None

    def pop_due(self, now: float | None = None) -> list[Reminder]:
        """Remove and return all reminders that are due (agent_node's poll)."""
        now = now or time.time()
        with self._lock:
            due = [r for r in self._reminders if r.due <= now]
            if due:
                self._reminders = [r for r in self._reminders if r.due > now]
                self._save()
            return sorted(due, key=lambda r: r.due)


# Module-level singleton — tools and agent_node share it (same pattern as _bridge).
_store: ReminderStore | None = None


def get_store() -> ReminderStore:
    global _store
    if _store is None:
        _store = ReminderStore()
    return _store


# ── Tools (bound to chat) ────────────────────────────────────────────────────

@tool
def set_reminder(text: str, in_minutes: Optional[float] = None,
                 at_time: Optional[str] = None, day: str = "today") -> str:
    """Schedule a reminder or timer. The robot announces `text` out loud when it fires.

    Give exactly ONE of:
      in_minutes — minutes from now ("remind me in 20 minutes", "5 minute timer")
      at_time    — 24h clock "HH:MM" ("remind me at 7pm" → at_time="19:00"),
                   with day="today" or "tomorrow". A today-time already past
                   rolls to tomorrow automatically.

    text should be the thing to announce, e.g. "Check the oven" or "Timer done"."""
    if (in_minutes is None) == (at_time is None):
        return "Error: give exactly one of in_minutes or at_time."
    if in_minutes is not None:
        if in_minutes <= 0:
            return "Error: in_minutes must be positive."
        due = time.time() + in_minutes * 60
    else:
        try:
            hh, mm = at_time.split(":")
            target = datetime.now().replace(hour=int(hh), minute=int(mm),
                                            second=0, microsecond=0)
        except (ValueError, AttributeError):
            return f'Error: at_time must be 24h "HH:MM", got {at_time!r}.'
        if day == "tomorrow":
            target += timedelta(days=1)
        elif target <= datetime.now():
            target += timedelta(days=1)  # past today → tomorrow
        due = target.timestamp()
    r = get_store().add(text.strip(), due)
    return f"Reminder #{r.id} set for {_fmt_due(r.due)}: {r.text}"


@tool
def list_reminders() -> str:
    """List all pending reminders and timers with their ids and due times."""
    active = get_store().active()
    if not active:
        return "No pending reminders."
    return "\n".join(f"#{r.id} at {_fmt_due(r.due)}: {r.text}" for r in active)


@tool
def cancel_reminder(reminder_id: int) -> str:
    """Cancel a pending reminder by its id (use list_reminders to find the id)."""
    r = get_store().cancel(reminder_id)
    if r is None:
        return f"No reminder #{reminder_id} found."
    return f"Cancelled reminder #{r.id}: {r.text}"
