"""speaker_volume() / bluetooth_device() / music() -- the owner's audio requests.
Pure zone.

Owner, 2026-10-04: "if I decrease or increase volume it needs to work",
"connect Bluetooth devices easily", "play songs, and if I say stop and ask
different questions it needs to answer just like a real assistant".
docs/voice/ASSISTANT_SCENARIOS.md S5-S12.

The work happens on the Pi 5's voice side: audio_device_node (volume,
Bluetooth -- /audio/cmd) and media_node (music -- /audio/music_cmd). These
tools only send a request through the bridge and speak the reply, which
already says what happened in words ("volume 70%", "now using OnePlus Buds
Z2", "playing Kesariya"). One volume knob for everything, music included: two
knobs ("speaker" vs "music") would have the model guessing which one "louder"
meant.

Capability-gated (CAP_MEDIA): guests may talk but not take over the speaker.
The "stop" word stops music by itself (media_node), with no turn at all.
No speaker / no player / no reply -> an honest sentence, never a raise.
"""

from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import permissions
from ._bridge import get as get_bridge

# Pairing scans for 15 s and then pairs; connecting may re-pair and switch
# profiles. Volume and music are quick.
_TIMEOUT_S = {"volume": 10.0, "status": 10.0, "connect": 35.0, "pair": 60.0, "music": 25.0}


def _refusal(state: dict | None, what: str) -> str | None:
    state = state or {}
    role = state.get("sender_role") or permissions.VOICE_ROLE
    if permissions.has_capability(role, permissions.CAP_MEDIA):
        return None
    who = state.get("sender_name") or "voice"
    return (f"Permission denied: {who} ({role}) may not change {what}. "
            "Politely refuse and offer to ask the owner.")


def _say(reply: dict | None) -> str:
    if not reply or "ok" not in reply:
        return ("The audio system did not answer -- it may be restarting. "
                "Tell the user to try again in a moment.")
    msg = (reply.get("msg") or "").strip()
    return msg if reply["ok"] else f"Could not do it: {msg}"


@tool
def speaker_volume(level: int = -1, change: int = 0, mute: bool | None = None,
                   state: Annotated[dict, InjectedState] = None) -> str:
    """The speaker's volume (music included). level=0-100 sets it ("volume 40");
    change=+10 / -10 for "louder" / "quieter" (+-25 for "much louder"); mute=True
    or False; no arguments reads the current level."""
    refusal = _refusal(state, "the volume")
    if refusal:
        return refusal
    if mute is not None:
        cmd = {"op": "mute", "on": bool(mute)}
    elif level is not None and level >= 0:
        cmd = {"op": "volume", "set": int(level)}
    else:
        cmd = {"op": "volume", "change": int(change or 0)}
    return _say(get_bridge().audio_request(cmd, timeout=_TIMEOUT_S["volume"]))


@tool
def bluetooth_device(action: Literal["status", "connect", "pair"] = "status", name: str = "",
                     state: Annotated[dict, InjectedState] = None) -> str:
    """Bluetooth speakers and headphones. status: which one is in use and which
    are paired. connect + name ("buds", "boAt"): switch to a paired one. pair
    (name optional): pair a NEW one -- it must be in pairing mode; takes ~20 s."""
    if action != "status":
        refusal = _refusal(state, "the Bluetooth device")
        if refusal:
            return refusal
    if action == "connect" and not name.strip():
        return "Ask which device to connect (call status to list the paired ones)."
    cmd = {"op": "bt", "action": {"status": "list"}.get(action, action), "name": name.strip()}
    return _say(get_bridge().audio_request(cmd, timeout=_TIMEOUT_S.get(action, 10.0)))


@tool
def music(action: Literal["play", "pause", "resume", "next", "stop", "status"],
          query: str = "", state: Annotated[dict, InjectedState] = None) -> str:
    """Music. play + query (a song, artist or mood) streams it; pause, resume,
    next, stop; status says what is playing. A spoken "stop" already stops
    music by itself."""
    if action != "status":
        refusal = _refusal(state, "the music")
        if refusal:
            return refusal
    if action == "play" and not query.strip():
        return "Ask what to play."
    cmd = {"op": action, "query": query.strip()} if action == "play" else {"op": action}
    return _say(get_bridge().music_request(cmd, timeout=_TIMEOUT_S["music"]))


AUDIO_TOOLS = [speaker_volume, bluetooth_device, music]
