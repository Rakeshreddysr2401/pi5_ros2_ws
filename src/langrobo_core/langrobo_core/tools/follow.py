"""follow_person — "follow me" and "come to me", on the Jetson's follow_node.

Bound to navigate only with LANGROBO_FOLLOW=1 (FOLLOW_AND_FAST_EYES.md): with
it off, no schema is shipped and every prompt is byte-identical to before.

The Jetson does the work (phase3/nodes/follow_node.py): it picks the person nearest
the middle of the camera view, keeps ~1 m from them at <= 0.3 m/s, checks the
way ahead every step, and stops on its own when they are lost or the way is
blocked. Here: who may ask, and the honest first answer.

  - The bridge WAITS for follow_node's first answer, so "nobody in view" is
    said now, not promised away ("Following you!" ... silence).
  - Nobody in view (2026-10-13, owner: "it needs to find them by rotating"):
    follow_node turns in place to FIND someone; the first answer is then
    "finding", the bridge says "There you are" when it finds them (voice), and
    a full circle with no one comes back as the [SYSTEM] report.
  - The person is photographed as the follow begins (photo log, source
    "follow"): ask_photos can say later who it followed.
  - Its end (lost / blocked / reached) comes back as the same [SYSTEM] report
    as an arrival (movement.nav_report), to whoever asked.
  - Stopping is not this tool's job: while the robot moves, an utterance with a
    stop word cancels the background drive (agent_node._on_user_input +
    utils/stop_words.py; other talk is answered with the follow still on), and
    that cancel reaches follow_node as /follow/cancel.
  - Over Telegram the sender is not in the room, so "follow me" is refused,
    by code. "come" still works there: it goes to the person the robot finds
    in the room ("find Mom and tell her dinner is ready", 2026-10-20 -- it
    used to be refused too, and the model then said "I am looking for her"
    four times while nothing moved). The camera cannot tell who someone is,
    so the report says "the person I found", and the asker gets a photo.
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
    "NOT started -- nothing moved: over Telegram you are not in the room, so I "
    "can't follow you. Tell the user that. (To go to someone who IS in the room, "
    "use mode=\"come\".)")


@tool
def follow_person(state: Annotated[dict, InjectedState],
                  mode: Literal["follow", "come"] = "follow",
                  then: str = "") -> str:
    """Follow the person in front of the robot, or come to them.

    mode="follow": "follow me", "come with me" -- keep about a metre behind
    them until they say stop, they are lost from view, or the way is blocked.
    mode="come": "come here", "come to me" -- drive up to about a metre from
    them, face them, and stop.

    It goes to the person nearest the middle of the camera view; with nobody
    in view it first turns around to find someone. It follows
    people only: asked to follow a pet or a thing, say you can only follow
    people for now. It does not know people by name: if the user named or
    described someone ("follow him", "follow the man in red"), say you will
    follow whoever is in front of you. Saying stop (or any stop word) ends it;
    other talk is answered while it keeps going.

    then: for mode="come", what to do on arriving ("come here and tell me the
    time" -> then="tell the user the time"). Leave empty otherwise.

    Over Telegram the sender is not in the room: mode="come" goes to the
    person the robot finds in the room -- "find Mom and tell her dinner is
    ready" -> mode="come", then="say out loud to her: Rakesh says dinner is
    ready". It cannot tell who people are, so it goes to whoever it finds;
    the sender gets a photo on arrival. "follow" is refused there.

    Returns at once with whether it started; a system message reports how it
    ended."""
    refusal = blocked_by_role(state) or blocked_by_manual()
    if refusal:
        return refusal
    remote = (state or {}).get("channel") == "telegram"
    if remote and mode != "come":
        return _TELEGRAM_REFUSAL

    remember_requester(state, then if mode == "come" else "")
    res = _bridge.get().start_follow(mode, announce=(state or {}).get("channel") == "voice",
                                     who="the person I found" if remote else "you")
    logger.info("follow_person(%s) -> %s", mode, res)
    if res.get("ok"):
        if remote:
            return (("I can't see anyone from here, so I'm turning around to find someone "
                     if res.get("result") == "finding" else
                     "I can see someone, so I'm going to them ")
                    + "-- I can't tell who people are, so it is whoever I find. The user "
                    "gets the arrival report and a photo automatically. Tell them that "
                    "in one sentence; do not say you have found or reached anyone yet.")
        if res.get("result") == "finding":
            return ("I can't see anyone yet, so I'm turning around to look for you. "
                    f"When I find you I'll say so and {'come to you' if mode == 'come' else 'follow you'}; "
                    "a system message will say how it ends.")
        if mode == "come":
            return ("Coming to you now: I'll stop about a metre away. "
                    "A system message will say when I'm there.")
        return ("Following you now, about a metre behind. Say stop when you want "
                "me to stop.")
    result = res.get("result")
    if result == "nobody" and remote:
        return ("NOT started: I can't see anyone in the room from here. Tell the user; "
                "if they know where the person is, they can send me to that place first.")
    if result == "nobody":
        return ("I can't see anyone in front of me to follow. Tell the user to "
                "stand in front of my camera and ask again. Do not turn or search.")
    if result == "unavailable":
        return (f"Following is not available right now: {res.get('why')}. "
                f"Say so; do not try to drive another way.")
    return f"I couldn't start: {res.get('why') or result}. Say so."
