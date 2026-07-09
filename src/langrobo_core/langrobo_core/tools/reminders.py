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
    repeat_minutes: float = 0.0   # 0 = one-shot; 1440 = daily, 10080 = weekly
    # Optional structured Telegram relay — set at creation time so the fire
    # path (agent_node._poll_reminders) can send it deterministically instead
    # of depending on the LLM remembering to call send_telegram_message when
    # this fires, possibly hours later. See tools/telegram.py for the actual
    # send (same gates apply — this only carries the recipient/flag along).
    telegram_recipient: Optional[str] = None
    telegram_report_back: bool = False


def _fmt_repeat(minutes: float) -> str:
    if minutes == 1440:
        return "daily"
    if minutes == 10080:
        return "weekly"
    if minutes % 60 == 0 and minutes >= 60:
        h = int(minutes // 60)
        return "every hour" if h == 1 else f"every {h} hours"
    return f"every {int(minutes)} minutes"


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

    def add(self, text: str, due: float, repeat_minutes: float = 0.0,
           telegram_recipient: Optional[str] = None,
           telegram_report_back: bool = False) -> Reminder:
        with self._lock:
            r = Reminder(id=self._next_id, text=text, due=due, created=time.time(),
                         repeat_minutes=repeat_minutes,
                         telegram_recipient=telegram_recipient,
                         telegram_report_back=telegram_report_back)
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
        """Return all due reminders (agent_node's poll). One-shots are removed;
        repeating ones are rescheduled to their next future occurrence — a long
        downtime yields ONE announcement, not a backlog."""
        now = now or time.time()
        with self._lock:
            due = [r for r in self._reminders if r.due <= now]
            if not due:
                return []
            fired = [Reminder(**asdict(r)) for r in due]  # snapshot: original due times
            keep = [r for r in self._reminders if r.due > now]
            for r in due:
                if r.repeat_minutes > 0:
                    while r.due <= now:
                        r.due += r.repeat_minutes * 60
                    keep.append(r)
            self._reminders = keep
            self._save()
            return sorted(fired, key=lambda r: r.due)


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
                 at_time: Optional[str] = None, day: str = "today",
                 repeat_minutes: float = 0.0,
                 telegram_recipient: Optional[str] = None,
                 telegram_report_back: bool = False) -> str:
    """Schedule a reminder or timer. The robot announces `text` out loud when it fires.

    Give exactly ONE of:
      in_minutes — minutes from now ("remind me in 20 minutes", "5 minute timer")
      at_time    — 24h clock "HH:MM" ("remind me at 7pm" → at_time="19:00"),
                   with day="today" or "tomorrow". A today-time already past
                   rolls to tomorrow automatically.

    repeat_minutes — 0 for one-shot (default); repeat interval otherwise:
      "every day at 21:00" → at_time="21:00", repeat_minutes=1440
      "every week"=10080, "every 2 hours"=120. Minimum 5.

    text should be the thing to announce, e.g. "Check the oven" or "Take your medicine".

    telegram_recipient — set ONLY when this reminder should ALSO relay a
    Telegram message to a household member when it fires (e.g. "this evening
    tell Mom to bring fruits" → text="Bring fruits home", at_time="18:00",
    telegram_recipient="Mom"). The relay is sent deterministically by code
    when the reminder fires — you don't need to remember to call
    send_telegram_message yourself later. If the recipient isn't a known
    Telegram member, leave this unset and rely on the spoken announcement only.
    telegram_report_back — mirrors send_telegram_message's report_back: True
    if the user wants the recipient's reply relayed back later."""
    if (in_minutes is None) == (at_time is None):
        return "Error: give exactly one of in_minutes or at_time."
    if repeat_minutes and repeat_minutes < 5:
        return "Error: repeat_minutes must be at least 5 (or 0 for one-shot)."
    if telegram_recipient:
        from ..services import telegram as telegram_service
        svc = telegram_service.get()
        if svc is None or not svc.configured():
            return "Error: Telegram is not set up on this robot."
        if svc.member_by_name(telegram_recipient) is None:
            return (f"Error: I don't know {telegram_recipient!r} on Telegram. "
                    f"Known members: {', '.join(svc.member_names())}.")
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
    r = get_store().add(text.strip(), due, repeat_minutes,
                        telegram_recipient=telegram_recipient,
                        telegram_report_back=telegram_report_back)
    rep = f", repeating {_fmt_repeat(repeat_minutes)}" if repeat_minutes else ""
    relay = f", relaying to {telegram_recipient} on Telegram" if telegram_recipient else ""
    return f"Reminder #{r.id} set for {_fmt_due(r.due)}{rep}{relay}: {r.text}"


@tool
def list_reminders() -> str:
    """List all pending reminders and timers with their ids and due times."""
    active = get_store().active()
    if not active:
        return "No pending reminders."
    return "\n".join(
        f"#{r.id} at {_fmt_due(r.due)}"
        + (f" (repeats {_fmt_repeat(r.repeat_minutes)})" if r.repeat_minutes else "")
        + f": {r.text}"
        for r in active)


@tool
def cancel_reminder(reminder_id: int) -> str:
    """Cancel a pending reminder by its id (use list_reminders to find the id)."""
    r = get_store().cancel(reminder_id)
    if r is None:
        return f"No reminder #{reminder_id} found."
    return f"Cancelled reminder #{r.id}: {r.text}"
