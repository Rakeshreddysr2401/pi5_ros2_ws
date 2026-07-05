"""Deterministic channel-confirmation gate for household relays. Pure zone.

Policy D10 (PRD): a bare "tell Mom X" must ask the sender back — her phone,
or say it aloud? — before anything is sent. The prompt states this, but the
12B model sometimes sends anyway, so the ENFORCEMENT lives here, in the
tools (same principle as permissions: a prompt can be sweet-talked, a gate
cannot). send_telegram_message and announce_at_home both call check() before
acting.

A relay may proceed when ANY of these hold:
  1. [SYSTEM] turn — a scheduled relay firing; there is nobody to ask.
  2. Errand forward — the turn carries the "[This may answer the errand …]"
     tag; the asker already chose the channel when they created the errand.
  3. The user's OWN words this turn name the channel ("text her", "on
     telegram", "say it aloud", "announce …").
  4. A pending ask exists from an EARLIER turn — i.e. the tool refused once,
     the robot asked, and the user has since spoken again. Whatever tool the
     model now picks is its reading of that answer.

Rule 4 is what makes this a real gate: confirmation cannot be minted inside
the same turn as the request, because "the user answered" is measured as a
new (non-camera) HumanMessage appearing in the shared history.
"""

from __future__ import annotations

import re
import threading

_EXPLICIT = {
    # The user named the phone channel: "text her", "message him", "on
    # telegram", "send it to her phone", "dm mom".
    "telegram": re.compile(
        r"\b(telegram|text|message|dm|on (?:her|his|their|my|the) phone|phone)\b",
        re.IGNORECASE),
    # The user asked for voice: "say it aloud", "announce", "out loud",
    # "tell her out loud", "speak it".
    "announce": re.compile(
        r"\b(aloud|out loud|announce|announcement|speak|say it|loudly)\b",
        re.IGNORECASE),
}

# An ask goes stale if the conversation moves on without an answer.
_PENDING_MAX_TURNS = 3

_lock = threading.Lock()
_pending: dict | None = None   # {"human_count": int} — one robot, one conversation


def _human_turn_count(messages) -> int:
    """Count real user turns. look()/photo captures also inject HumanMessages
    (image blocks) mid-turn — those must not count as 'the user spoke'."""
    count = 0
    for m in messages or []:
        if type(m).__name__ != "HumanMessage":
            continue
        content = getattr(m, "content", "")
        if isinstance(content, list):
            if any(isinstance(p, dict) and p.get("type") == "image_url"
                   for p in content):
                continue
            content = " ".join(p.get("text", "") for p in content
                               if isinstance(p, dict))
        count += 1 if str(content).strip() else 0
    return count


def _last_human_text(messages) -> str:
    for m in reversed(messages or []):
        if type(m).__name__ != "HumanMessage":
            continue
        content = getattr(m, "content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content
                               if isinstance(p, dict) and p.get("type") == "text")
        return str(content)
    return ""


def check(state: dict, kind: str, ask_hint: str) -> str | None:
    """Gate one relay attempt. Returns None when the send/announcement may
    proceed, or the refusal string the tool must return verbatim (it tells
    the model to ask the user and NOT to retry this turn)."""
    global _pending
    if state.get("channel") == "system":
        return None                                    # scheduled — nobody to ask
    last = _last_human_text(state.get("messages"))
    if "[This may answer the errand" in last:
        return None                                    # errand forward — channel chosen earlier
    if _EXPLICIT[kind].search(last):
        with _lock:
            _pending = None
        return None                                    # user named the channel themselves
    now = _human_turn_count(state.get("messages"))
    with _lock:
        if _pending is not None:
            age = now - _pending["human_count"]
            if 1 <= age <= _PENDING_MAX_TURNS:
                _pending = None                        # the user answered — proceed
                return None
        _pending = {"human_count": now}
    return (f"NOT done yet — the user hasn't said HOW to deliver this. Ask "
            f"them ONE short question now ({ask_hint}) and end your turn. "
            f"When they answer, call the tool matching their choice. Do not "
            f"call this tool again in this same turn.")


def reset() -> None:
    """Test hook — clear any pending ask."""
    global _pending
    with _lock:
        _pending = None
