"""memory() -- what the household tells the robot, kept across reboots. Pure zone.

The store is services/memory.py. Owner and family only (CAP_MEMORY): a guest on
Telegram must not read back the family's facts, or erase them.
"""

import time
from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import memory as store
from ..services import permissions


def _when(ts: float) -> str:
    days = int((time.time() - ts) // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    return time.strftime("%B %Y", time.localtime(ts))


@tool
def memory(action: Literal["remember", "recall", "forget"], text: str,
           state: Annotated[dict, InjectedState] = None) -> str:
    """Long-term memory of what people TELL you, kept across reboots. remember:
    store one fact as a full sentence with names ("Amma takes her BP pills at
    9 pm"). recall: search it ("what does Amma drink?") before saying you don't
    know something personal. forget: delete a fact that is wrong or changed
    (then remember the new one)."""
    state = state or {}
    role = state.get("sender_role") or permissions.VOICE_ROLE
    if not permissions.has_capability(role, permissions.CAP_MEMORY):
        who = state.get("sender_name") or "this person"
        return (f"Permission denied: {who} ({role}) may not use the household memory. "
                "Politely say you can't share or change that.")
    text = (text or "").strip()
    if not text:
        return f"Ask what to {action}."
    if action == "remember":
        how, fact = store.remember(text, who=state.get("sender_name"))
        return f"Remembered: {fact}" if how == "added" else f"Already knew that: {fact}"
    if action == "forget":
        gone = store.forget(text)
        if gone is None:
            hits = store.recall(text, k=3)
            if not hits:
                return "Nothing like that is remembered."
            return ("Not sure which one to forget -- ask the user which: "
                    + " | ".join(h["text"] for h in hits))
        return f"Forgot: {gone['text']}"
    hits = store.recall(text)
    if not hits:
        return "Nothing remembered about that."
    return "\n".join(
        f"- {h['text']} ({'told by ' + h['who'] + ', ' if h['who'] else ''}{_when(h['updated'])})"
        for h in hits)


MEMORY_TOOLS = [memory]
