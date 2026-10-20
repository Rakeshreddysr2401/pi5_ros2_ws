"""Reports that CODE delivers to whoever asked -- pure zone.

A background search finding (or not finding) its object, a drive that ended:
these are facts the robot already has as a sentence. Sending them through
an LLM turn cost 5-10 s each and let the model rewrite them -- on 2026-10-20
the report "I've arrived near the dining table. But I can't see the dining
table in front of me now" reached the phone as "I have arrived at the dining
table." So a notice goes out as written: spoken (voice) or sent to the
asker's chat (Telegram), and appended to the history so the agents know it.

agent_node registers the sink (it owns the speaker, the Telegram service and
the history, and delivers on its worker thread, between turns). With no sink
-- Studio, tests -- the tools keep their old blocking behaviour, so nothing
here is required for the brain to work.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# sink(text, requester, report=..., photo=...) -> None
_sink: Callable | None = None


def set_sink(fn: Callable | None) -> None:
    global _sink
    _sink = fn


def available() -> bool:
    return _sink is not None


def requester_of(state: dict | None) -> dict:
    """Who a notice goes to: the same fields movement.remember_requester keeps."""
    state = state or {}
    return {"channel": state.get("channel") or "voice",
            "sender": state.get("sender_name") or "voice",
            "role": state.get("sender_role")}


def post(text: str, requester: dict, report: str = "", photo: bool = False) -> bool:
    """Queue `text` for the person in `requester`. `report` is the [SYSTEM]
    line kept in the history beside it ("" -> derived from text); `photo`: a
    Telegram asker also gets the current camera view, with text as caption.
    False when there is no sink (the caller then says it some other way)."""
    if _sink is None or not (text or "").strip():
        return False
    try:
        _sink(text.strip(), requester or {}, report=report, photo=photo)
        return True
    except Exception as e:             # noqa: BLE001 -- a report must never raise into a tool
        logger.warning("notice not queued: %s", e)
        return False
