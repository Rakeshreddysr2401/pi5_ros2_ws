"""announce_at_home() — speak a message aloud in the house, from Telegram.

Closes the gap the reply-sink design creates on purpose: a Telegram turn's
reply goes to the sender's chat and never the speaker, so "tell Mom I'm
coming late — say it out loud" had no path. This tool rides the standard
[SYSTEM] producer pattern (CLAUDE.md rule 5): it enqueues a system turn via
the bridge; the normal proactive-speech path phrases and speaks it, and the
announcement lands in the shared history like every other utterance.

Policy (decided 2026-07-06):
  - Capability-gated: CAP_ANNOUNCE (owner/family). A guest must not use the
    robot as a megaphone into someone's living room.
  - Quiet hours REFUSE with alternatives (never wake the house by default);
    the sender's explicit insistence sets override_quiet_hours.
  - Voice callers don't need it — their reply already IS speech.
"""

import logging
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import permissions
from ..services import telegram as telegram_service
from ._bridge import get as get_bridge

logger = logging.getLogger(__name__)


@tool
def announce_at_home(message: str, state: Annotated[dict, InjectedState],
                     override_quiet_hours: bool = False) -> str:
    """Say a message OUT LOUD in the house, on the robot's speaker.

    For Telegram senders who want the household to HEAR something ("tell Mom
    aloud that I'm coming late", "announce that dinner is ready"). Write
    `message` as the robot speaking on the sender's behalf, e.g. "Rakesh says
    he'll be home late." For a private message to one person's phone use
    send_telegram_message instead.

    Set override_quiet_hours=True ONLY when the sender explicitly insists
    after being told it is quiet hours ("announce it anyway")."""
    sender = state.get("sender_name") or "voice"
    role = state.get("sender_role") or permissions.VOICE_ROLE
    message = (message or "").strip()
    if not message:
        return "There is nothing to announce — ask the sender what to say."
    if state.get("channel") not in ("telegram",):
        # A voice caller is already talking to the room; their reply IS the
        # announcement. Don't double-speak through the system queue.
        return ("You are speaking with the household directly — just say it "
                "in your reply instead of using this tool.")
    if not permissions.has_capability(role, permissions.CAP_ANNOUNCE):
        logger.info("AUDIT announce sender=%s outcome=denied", sender)
        return (f"Permission denied: {sender} ({role}) is not allowed to make "
                f"announcements in the home. Politely refuse and offer to ask "
                f"the owner instead.")

    svc = telegram_service.get()
    if svc is not None and svc.quiet_now() and not override_quiet_hours:
        logger.info("AUDIT announce sender=%s outcome=quiet_hours", sender)
        return ("It is quiet hours in the house right now, so you did NOT "
                "announce it. Ask the sender: should you send it to their "
                "phone on Telegram instead, wait until quiet hours end, or "
                "announce it anyway? Only if they insist on announcing now, "
                "call this tool again with override_quiet_hours=True.")

    get_bridge().enqueue_system_turn(
        f"[SYSTEM] {sender} asks (via Telegram) to announce this aloud to the "
        f'household right now: "{message}" — say it naturally and briefly.')
    logger.info("AUDIT announce sender=%s outcome=queued override=%s",
                sender, override_quiet_hours)
    return (f"Queued — the robot will say it aloud in the house within a few "
            f"seconds. Confirm to {sender} that it is being announced.")
