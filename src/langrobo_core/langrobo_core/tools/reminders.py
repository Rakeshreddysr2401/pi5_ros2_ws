"""set_reminder() / reminders() -- timers, alarms and reminders. Pure zone.

The list lives in services/alarms.py; agent_node rings what is due (spoken,
twice for timers and alarms unless "stop" is said). These tools only add,
list and cancel. Anyone in the house may set one (CAP_CHAT): a guest's timer
for the tea is not a security question.
"""

from typing import Literal

from langchain_core.tools import tool

from ..services import alarms


@tool
def set_reminder(kind: Literal["timer", "alarm", "reminder"], minutes: float = 0, at: str = "",
                 label: str = "", daily: bool = False) -> str:
    """A timer, alarm or reminder that rings out loud. minutes from now (1.5 =
    90 s) or at="18:30" / "6:30 am" (local; a time already passed today means
    tomorrow). label: what it is for ("tea", "take medicine"). daily=True
    repeats every day."""
    try:
        due = alarms.parse_due(minutes, at)
    except ValueError as e:
        return f"Could not set it: {e}. Ask the user when."
    item = alarms.add(kind, due, label, daily)
    return f"Set: {alarms.describe(item)}."


@tool
def reminders(action: Literal["list", "cancel"] = "list", which: str = "") -> str:
    """The timers, alarms and reminders that are set (list), or cancel: which =
    a label word ("tea"), a kind ("timers"), or "all"."""
    if action == "cancel":
        if not (which or "").strip():
            return "Ask which one to cancel (or 'all')."
        gone = alarms.cancel(which)
        if not gone:
            left = alarms.pending()
            return ("Nothing matched. Set now: " + "; ".join(alarms.describe(i) for i in left)
                    if left else "There is nothing set.")
        return "Cancelled: " + "; ".join(alarms.describe(i) for i in gone) + "."
    items = alarms.pending()
    if not items:
        return "There are no timers, alarms or reminders set."
    return "Set: " + "; ".join(alarms.describe(i) for i in items) + "."


REMINDER_TOOLS = [set_reminder, reminders]
