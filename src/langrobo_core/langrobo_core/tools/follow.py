"""follow_person — "follow me" and "come to me", on the Jetson's follow_node.

Bound to navigate only with LANGROBO_FOLLOW=1 (FOLLOW_AND_FAST_EYES.md): with
it off, no schema is shipped and every prompt is byte-identical to before.

The Jetson does the work (phase3/nodes/follow.py): it picks the person nearest
the middle of the camera view, keeps ~1 m from them at <= 0.3 m/s, checks the
way ahead every step, and stops on its own when they are lost or the way is
blocked. Here: who may ask, and the honest first answer.

  - The bridge WAITS for follow_node's first answer, so "nobody in view" is
    said now, not promised away ("Following you!" ... silence).
  - Its end (lost / blocked / reached) comes back as the same [SYSTEM] report
    as an arrival (movement.nav_report), to whoever asked.
  - Stopping is not this tool's job: every utterance already stops the wheels
    and cancels the background drive (agent_node._on_user_input), and that
    cancel reaches follow_node as /follow/cancel.
  - Over Telegram "me" is not someone the camera can see: refused, by code.
  - Role and teleop MANUAL: the same guards as every drive.
"""

import logging
import os
from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from . import _bridge
from .movement import blocked_by_manual, blocked_by_role, remember_requester

logger = logging.getLogger(__name__)

FOLLOW_ENABLED = os.environ.get("LANGROBO_FOLLOW", "0").strip().lower() not in (
    "", "0", "false", "no", "off")

_TELEGRAM_REFUSAL = (
    "I can only follow or come to a person my camera can see, and over Telegram "
    "I can't tell which person you are. Tell the user that; if they want me "
    "somewhere, they can name a saved place instead.")


@tool
def follow_person(state: Annotated[dict, InjectedState],
                  mode: Literal["follow", "come"] = "follow",
                  then: str = "") -> str:
    """Follow the person in front of the robot, or come to them.

    mode="follow": "follow me", "come with me" -- keep about a metre behind
    them until they say stop, they are lost from view, or the way is blocked.
    mode="come": "come here", "come to me" -- drive up to about a metre from
    them, face them, and stop.

    It goes to the person nearest the middle of the camera view; it cannot
    tell people apart by name or follow animals. If the user named someone
    ("follow him", "follow the man in red"), say you will follow whoever is in
    front of you. Any new sentence from the user stops it.

    then: for mode="come", what to do on arriving ("come here and tell me the
    time" -> then="tell the user the time"). Leave empty otherwise.

    Returns at once with whether it started; a system message reports how it
    ended."""
    refusal = blocked_by_role(state) or blocked_by_manual()
    if refusal:
        return refusal
    if (state or {}).get("channel") == "telegram":
        return _TELEGRAM_REFUSAL

    remember_requester(state, then if mode == "come" else "")
    res = _bridge.get().start_follow(mode)
    logger.info("follow_person(%s) -> %s", mode, res)
    if res.get("ok"):
        if mode == "come":
            return ("Coming to you now: I'll stop about a metre away. "
                    "A system message will say when I'm there.")
        return ("Following you now, about a metre behind. Say stop when you want "
                "me to stop.")
    result = res.get("result")
    if result == "nobody":
        return ("I can't see anyone in front of me to follow. Tell the user to "
                "stand in front of my camera and ask again. Do not turn or search.")
    if result == "unavailable":
        return (f"Following is not available right now: {res.get('why')}. "
                f"Say so; do not try to drive another way.")
    return f"I couldn't start: {res.get('why') or result}. Say so."
