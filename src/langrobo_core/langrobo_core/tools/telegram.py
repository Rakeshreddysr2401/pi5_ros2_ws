"""send_telegram_message() / send_telegram_photo() — reach household members'
phones. Pure zone.

Capability-gated (services/permissions.py): the sender's identity rides on the
turn state (channel / sender_name / sender_role — set per-turn by agent_node
for Telegram turns in Phase B). Voice turns carry no identity yet and run as
the owner. Checks live HERE, in the tool, not in prompts.

Audit: every attempt logs sender → recipient, capability, and outcome — never
the message text (it's the household's private chatter).
"""

import logging
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import permissions
from ..services import telegram as telegram_service
from ._bridge import get as get_bridge

logger = logging.getLogger(__name__)


def _sender(state: dict) -> tuple[str, str]:
    return (state.get("sender_name") or "voice",
            state.get("sender_role") or permissions.VOICE_ROLE)


def _audit(sender: str, capability: str, recipient: str, outcome: str) -> None:
    logger.info("AUDIT telegram capability=%s sender=%s recipient=%s outcome=%s",
                capability, sender, recipient, outcome)


def _gate(state: dict, capability: str, recipient: str):
    """Resolve (service, member) or an honest refusal string for the model."""
    sender, role = _sender(state)
    svc = telegram_service.get()
    if svc is None or not svc.configured():
        return None, None, ("Telegram messaging is not set up on this robot. "
                            "Tell the user it is unavailable.")
    if not permissions.has_capability(role, capability):
        _audit(sender, capability, recipient, "denied")
        return None, None, (
            f"Permission denied: {sender} ({role}) is not allowed to use "
            f"'{capability}'. Politely refuse and offer to ask the owner instead.")
    member = svc.member_by_name(recipient)
    if member is None:
        return None, None, (f"I don't know {recipient!r} on Telegram. "
                            f"Known members: {', '.join(svc.member_names())}.")
    return svc, member, None


@tool
def send_telegram_message(recipient: str, message: str,
                          state: Annotated[dict, InjectedState],
                          report_back: bool = False) -> str:
    """Send a text message to a household member's phone via Telegram.

    Use for relaying ("tell Mom I'll be late" → recipient="Mom") or notifying
    someone who isn't in the room. Write `message` as the robot speaking on the
    sender's behalf, e.g. "Rakesh says he'll be late today." Keep it short and
    natural — it lands as a phone message.

    Set report_back=True when the user wants to hear the answer ("ask Mom …",
    "tell her X and let me know what she says") — the recipient's reply will
    then come back to you marked as the answer to this errand, even hours
    later, so you can pass it on."""
    from .errands import get_store as get_errand_store
    sender, _ = _sender(state)
    svc, member, refusal = _gate(state, permissions.CAP_RELAY, recipient)
    if refusal:
        return refusal
    if state.get("channel") == "system" and svc.quiet_now():
        # Proactive pings ([SYSTEM] turns: reminders, deliveries) respect quiet
        # hours; a person's direct request always goes through.
        svc.defer(member.chat_id, message)
        _audit(sender, permissions.CAP_RELAY, member.name, "deferred")
        return (f"It's quiet hours — the message to {member.name} was queued "
                f"and will be delivered once quiet hours end.")
    err = svc.send_message(member.chat_id, message)
    _audit(sender, permissions.CAP_RELAY, member.name, "error" if err else "sent")
    if err:
        return f"{err} Tell the user the message to {member.name} did not go through."
    if report_back:
        via = state.get("channel") or "voice"
        asked_by = state.get("sender_name") or "the user"
        get_errand_store().add(asked_via=via, asked_by=asked_by,
                               sent_to=member.name, gist=message)
        return (f"Message delivered to {member.name} on Telegram. Their reply "
                f"will be routed back to you as the answer to this errand.")
    return f"Message delivered to {member.name} on Telegram."


@tool
def send_telegram_photo(recipient: str, caption: str,
                        state: Annotated[dict, InjectedState]) -> str:
    """Snap the robot's current camera view and send it to a household member's
    phone via Telegram.

    Use when someone asks for a photo of what the robot sees ("send me a pic of
    the room"). The photo is taken fresh from the robot's current position —
    the robot cannot go somewhere else first yet. `caption` is a short line
    describing the shot; pass "" for none."""
    sender, _ = _sender(state)
    svc, member, refusal = _gate(state, permissions.CAP_PHOTO, recipient)
    if refusal:
        return refusal
    # Same staleness contract as look(): a continuously-publishing camera whose
    # last frame is old means the feed is down — admit blindness, don't send
    # a long-gone scene as "current".
    frame = get_bridge().get_frame(max_age_s=10.0)
    if frame is None:
        _audit(sender, permissions.CAP_PHOTO, member.name, "no_frame")
        return ("No current camera frame is available — the camera feed appears "
                "to be down. Tell the user you cannot take a photo right now.")
    err = svc.send_photo(member.chat_id, frame, caption)
    _audit(sender, permissions.CAP_PHOTO, member.name, "error" if err else "sent")
    if err:
        return f"{err} Tell the user the photo to {member.name} did not go through."
    return f"Photo sent to {member.name} on Telegram."


TELEGRAM_TOOLS = [send_telegram_message, send_telegram_photo]
