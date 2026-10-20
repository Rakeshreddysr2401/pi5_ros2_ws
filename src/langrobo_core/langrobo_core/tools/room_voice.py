"""say_in_room() -- speak through the robot's speaker for someone who is NOT
in the room. Pure zone.

CLAUDE.md rule 4 still holds for the people in the room: on a voice turn the
reply IS the speech, so this tool refuses there. It exists for the other
channel: a Telegram turn's reply goes to the phone and never reaches the
speaker, so "tell her loudly to bring water" had no way out -- on 2026-10-20
the model answered "I will tell her loudly" and nothing was said. Also for a
Telegram-requested drive's errand on arrival ("...go to Mom and tell her"),
which runs as a quiet [SYSTEM] turn on that sender's behalf.

Capability-gated as relaying a message (permissions.CAP_RELAY): a guest may
not talk through the house's speaker.
"""

import logging
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import permissions
from . import _bridge

logger = logging.getLogger(__name__)


def _remote(state: dict) -> bool:
    """A turn whose reply does not reach the speaker: Telegram, or a [SYSTEM]
    turn acting for a Telegram sender (agent_node sets sender_name only then)."""
    channel = state.get("channel")
    return channel == "telegram" or (channel == "system" and bool(state.get("sender_name")))


@tool
def say_in_room(message: str, state: Annotated[dict, InjectedState]) -> str:
    """Say `message` OUT LOUD through the robot's speaker, to the people in the
    room. Only for a Telegram sender (who is not in the room), or an errand
    one of them gave for arrival: "tell Mom dinner is ready" while she is near
    the robot, "say it loudly", "go to her and tell her ...". Write it as the
    robot speaking on their behalf: "Rakesh says: please bring water."
    In a spoken conversation your reply is already said aloud -- not needed."""
    state = state or {}
    sender = state.get("sender_name") or "voice"
    role = state.get("sender_role") or permissions.VOICE_ROLE
    if not _remote(state):
        return ("Not needed: this is a spoken conversation, so your reply is "
                "already said aloud. Just reply with what to say.")
    if not permissions.has_capability(role, permissions.CAP_RELAY):
        logger.info("AUDIT capability=relay_aloud sender=%s role=%s outcome=denied", sender, role)
        return (f"NOT said: {sender} ({role}) is not allowed to speak through the "
                f"robot. Politely refuse.")
    text = " ".join((message or "").split())
    if not text:
        return "NOT said: the message was empty."
    try:
        _bridge.get().publish_speech(text)
    except Exception as e:                 # noqa: BLE001 -- say what happened, never raise
        return f"NOT said -- the speaker is not available ({type(e).__name__}). Tell the user."
    logger.info("AUDIT capability=relay_aloud sender=%s outcome=spoken", sender)
    return f"Said out loud in the room: \"{text}\"."
