"""send_telegram_message() / send_telegram_photo() — reach household members'
phones. Pure zone.

This is how you talk to the robot today: the Pi 5 has no microphone or speaker
attached (see the rover repo's FLEET_STATUS.md), so Telegram is the working
channel and voice is the one waiting on hardware.

Capability-gated (services/permissions.py): the sender's identity rides on the
turn state (channel / sender_name / sender_role — set per-turn by agent_node
for Telegram turns). Voice turns carry no identity yet and run as the owner.
Checks live HERE, in the tool, not in prompts — a prompt is a suggestion.

Audit: every attempt logs sender → recipient, capability and outcome — never
the message text (it's the household's private chatter).
"""

import logging
import time
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
    member = svc.member_by_name(recipient) or _self_member(svc, state, recipient)
    if member is None:
        _audit(sender, capability, recipient, "unknown_recipient")
        # "NOT sent" in so many words: with a softer line the model told the
        # user "I have sent a photo to Rakesh" -- nothing had gone (2026-10-04).
        return None, None, (f"NOT sent -- nothing went to anyone: I don't know "
                            f"{recipient!r} on Telegram. Known members: "
                            f"{', '.join(svc.member_names())}. Tell the user it was "
                            f"not sent and ask who they meant.")
    return svc, member, None


# How people name THEMSELVES as the recipient ("send me the picture"; the owner
# is addressed as "Boss", 2026-10-04). Names, not intent: this only resolves who
# "me" is, after the model has already chosen to send.
_SELF_NAMES = {"me", "myself", "boss", "the boss", "owner", "the owner", "user",
               "the user"}


def _self_member(svc, state: dict, recipient: str):
    """The asker, when the recipient is how people name themselves: the
    Telegram sender if they are a member, else (voice, Studio) the one owner."""
    if recipient.strip().casefold() not in _SELF_NAMES:
        return None
    if state.get("channel") == "telegram" and state.get("sender_name"):
        return svc.member_by_name(state["sender_name"])
    owners = svc.members_with_role("owner")
    return owners[0] if len(owners) == 1 else None


@tool
def send_telegram_message(recipient: str, message: str,
                          state: Annotated[dict, InjectedState]) -> str:
    """Send a text message to a household member's phone via Telegram.

    Use for relaying ("tell Mom I'll be late" → recipient="Mom") or notifying
    someone who isn't in the room. Write `message` as the robot speaking on the
    sender's behalf, e.g. "Rakesh says he'll be late today." Keep it short and
    natural — it lands as a phone message."""
    sender, _ = _sender(state)
    svc, member, refusal = _gate(state, permissions.CAP_RELAY, recipient)
    if refusal:
        return refusal
    if state.get("channel") == "system" and svc.quiet_now():
        # Proactive pings ([SYSTEM] turns: nav arrival) respect quiet hours; a
        # person's direct request always goes through.
        svc.defer(member.chat_id, message)
        _audit(sender, permissions.CAP_RELAY, member.name, "deferred")
        return (f"It's quiet hours — the message to {member.name} was queued "
                f"and will be delivered once quiet hours end.")
    err = svc.send_message(member.chat_id, message)
    _audit(sender, permissions.CAP_RELAY, member.name, "error" if err else "sent")
    if err:
        if err.startswith(telegram_service.QUEUED_PREFIX):
            return err            # it WILL arrive: say that, not "did not go through"
        return f"{err} Tell the user the message to {member.name} did not go through."
    return f"Message delivered to {member.name} on Telegram."


@tool
def send_telegram_photo(recipient: str, caption: str,
                        state: Annotated[dict, InjectedState]) -> str:
    """Snap the robot's current camera view and send it to a household member's
    phone via Telegram.

    Use when someone asks for a photo of what the robot sees ("send me a pic of
    the room"). The photo is taken fresh from the robot's current position —
    the robot cannot go somewhere else first. `caption` is a short line
    describing the shot; pass "" for none."""
    sender, _ = _sender(state)
    svc, member, refusal = _gate(state, permissions.CAP_PHOTO, recipient)
    if refusal:
        return refusal
    bridge = get_bridge()
    if getattr(bridge, "navigation_active", lambda: False)():
        # "Go to the bag and send me a pic": approach returns as soon as the
        # drive STARTS, and the model then sent the photo at once, captioned
        # "I have reached the black bag" -- a picture of the way there, and a
        # drive that then failed (2026-09-27). Held here instead, and sent by
        # the brain itself when the arrival report comes (send_pending_photo);
        # dropped if the drive fails. Code, not a prompt rule: the model was
        # already told the drive continues in the background.
        global _pending_photo
        _pending_photo = {"chat_id": member.chat_id, "name": member.name,
                          "caption": caption, "sender": sender, "at": time.time()}
        _audit(sender, permissions.CAP_PHOTO, member.name, "held_until_arrival")
        return (f"Not sent yet: I am still driving, so a photo now would show the "
                f"way, not the place. It will go to {member.name} automatically "
                f"when I arrive (and not at all if I can't get there). Tell them "
                f"that; do not say you have arrived.")
    # Same staleness contract as look(): a continuously-publishing camera whose
    # last frame is old means the feed is down — admit blindness, don't send
    # a long-gone scene as "current".
    frame = bridge.get_frame(max_age_s=10.0)
    if frame is None:
        _audit(sender, permissions.CAP_PHOTO, member.name, "no_frame")
        return ("No current camera frame is available — the camera feed appears "
                "to be down. Tell the user you cannot take a photo right now.")
    err = svc.send_photo(member.chat_id, frame, caption)
    _audit(sender, permissions.CAP_PHOTO, member.name, "error" if err else "sent")
    if err:
        return f"{err} Tell the user the photo to {member.name} did not go through."
    return f"Photo sent to {member.name} on Telegram."


# A photo asked for while the robot was still driving (send_telegram_photo).
_pending_photo: dict | None = None
_PENDING_PHOTO_MAX_AGE_S = 600.0   # a drive cancelled without a report must not
                                   # leave a photo to fire at some later arrival


def send_pending_photo(arrived: bool) -> str | None:
    """Called by agent_node when a drive reports. On arrival, sends the photo
    held by send_telegram_photo and returns a note for the report turn (so the
    model does not send a second one); on failure, drops it. None when there
    was nothing held."""
    global _pending_photo
    held, _pending_photo = _pending_photo, None
    if not held or time.time() - held["at"] > _PENDING_PHOTO_MAX_AGE_S:
        return None
    if not arrived:
        _audit(held["sender"], permissions.CAP_PHOTO, held["name"], "dropped_nav_failed")
        return (f"The photo {held['name']} asked for was NOT sent, because I did "
                f"not get there.")
    time.sleep(1.0)    # let the camera settle on the arrival view
    frame = get_bridge().get_frame(max_age_s=3.0)
    svc = telegram_service.get()
    err = ("no fresh camera frame" if frame is None
           else svc.send_photo(held["chat_id"], frame, held["caption"]) if svc else "telegram is off")
    _audit(held["sender"], permissions.CAP_PHOTO, held["name"], "error" if err else "sent_on_arrival")
    if err:
        return f"The photo {held['name']} asked for could not be sent ({err})."
    return f"The photo {held['name']} asked for has been sent from here; do not send another."


TELEGRAM_TOOLS = [send_telegram_message, send_telegram_photo]
