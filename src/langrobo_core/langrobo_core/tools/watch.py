"""watch_home() — arm/disarm home watch mode. Pure zone.

Capability-gated (services/permissions.py CAP_WATCH): owner and family may
arm/disarm; a guest may not — disarming is the sensitive half (a guest-level
sender must never be able to switch the alarm off). Checks live HERE, in the
tool, never only in prompts.

The detection loop itself lives in agent_node's watch poll timer (it needs
the bridge + the Jetson target finder); this tool only flips the persisted
armed state and reports sensor health honestly at arm time.
"""

import logging
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import permissions
from ..services import telegram as telegram_service
from ..services import watch as watch_service
from ._bridge import get as get_bridge

logger = logging.getLogger(__name__)


def watch_context() -> str:
    """Dynamic prompt block for the chat agent — present only while armed
    (mirrors the NOW PLAYING pattern: rare state changes, cheap re-prefill)."""
    svc = watch_service.get()
    if svc is None or not svc.armed():
        return ""
    return ("\n== WATCH ==\nHome watch mode is ARMED — a person seen by the "
            "camera triggers a photo alert to the owner's phone. "
            '"stop watching" / "I\'m back" → watch_home(False).\n')


@tool
def watch_home(enable: bool, state: Annotated[dict, InjectedState]) -> str:
    """Arm or disarm home watch mode.

    While armed, the robot keeps looking for people; when someone is seen it
    immediately sends a photo to the owner's phone (Telegram) and mentions it
    aloud. Use when the user says "watch the house", "keep an eye out while
    I'm gone" (enable=True) or "stop watching", "I'm back" (enable=False)."""
    sender = state.get("sender_name") or "voice"
    role = state.get("sender_role") or permissions.VOICE_ROLE
    svc = watch_service.get()
    if svc is None or not svc.enabled():
        return "Home watch mode is disabled on this robot (LANGROBO_WATCH=false)."
    if not permissions.has_capability(role, permissions.CAP_WATCH):
        logger.info("AUDIT watch capability=watch sender=%s outcome=denied", sender)
        return (f"Permission denied: {sender} ({role}) is not allowed to arm or "
                f"disarm home watch. Politely refuse and offer to ask the owner.")

    if not enable:
        svc.disarm(sender)
        get_bridge().set_vision_target("")   # stop the Jetson person hunt now
        logger.info("AUDIT watch sender=%s outcome=disarmed", sender)
        return "Watch mode is off. Tell the user you've stopped watching."

    svc.arm(sender)
    get_bridge().set_vision_target("person")  # start hunting without waiting for the poll
    logger.info("AUDIT watch sender=%s outcome=armed", sender)
    caveats = []
    frame_age = get_bridge().frame_age()
    if frame_age is None or frame_age > 10.0:
        caveats.append("the camera feed looks down right now, so alerts can't "
                       "include a photo until it's back")
    tg = telegram_service.get()
    if tg is None or not tg.configured():
        caveats.append("Telegram isn't set up, so I can only announce aloud — "
                       "no phone alerts")
    if caveats:
        return ("Watch mode is armed, BUT " + "; and ".join(caveats) +
                ". Tell the user this honestly.")
    return ("Watch mode is armed — a photo goes to the owner's phone whenever "
            "I see someone. Confirm this to the user briefly.")
