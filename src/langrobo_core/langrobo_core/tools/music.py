"""Music tools — pure zone. The speaker lives on the Jetson, so these tools
only publish commands on /audio/music_cmd (JSON) and read the cached
/audio/music_state the Jetson music_node reports back. See
JETSON_VOICE_UPGRADE.md for the music_node contract.

Instant stop does NOT depend on this path: the Jetson's local stop-spotter
halts playback in <400ms on the spoken "stop" keyword without a round-trip
through the brain. stop_music() is the conversational path ("please stop the
music") and the cleanup path.
"""

import json
import time

from langchain_core.tools import tool

from . import _bridge

# How long play_music waits for the Jetson to confirm playback started
# (yt-dlp resolution + stream buffering typically takes 2-6s).
_PLAY_CONFIRM_TIMEOUT_S = 10.0
_POLL_S = 0.25


def _await_state(bridge, predicate, timeout: float):
    """Poll the cached music state until predicate(state) or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = bridge.get_music_state()
        if state and predicate(state):
            return state
        time.sleep(_POLL_S)
    return None


@tool
def play_music(query: str) -> str:
    """Play a song or music. `query` is what to play — a song name, artist,
    genre or mood ("play Shape of You", "some calm piano music", "80s rock").

    Playback happens on the robot's speaker. Returns what actually started
    playing, so confirm it to the user in your reply."""
    bridge = _bridge.get()
    sent_at = time.time()
    seq_before = bridge.get_music_state_seq()
    bridge.music_command({"action": "play", "query": query.strip(), "t": sent_at})

    def _confirms(s: dict) -> bool:
        if not (s.get("playing") or s.get("error")):
            return False
        # music_node echoes the command's `t` back as cmd_t — an opaque token,
        # so confirmation never compares the Jetson's clock against ours
        # (the two drift ~1.5s) and a heartbeat of a previous song can't
        # masquerade as this request starting.
        if "cmd_t" in s:
            return s["cmd_t"] == sent_at
        # Older music_node without cmd_t: any state received after we sent.
        return bridge.get_music_state_seq() > seq_before

    state = _await_state(bridge, _confirms, _PLAY_CONFIRM_TIMEOUT_S)
    if state is None:
        return (f"Asked the speaker to play '{query}' but got no confirmation — "
                "the music player may be offline. Tell the user honestly.")
    if state.get("error"):
        return f"Couldn't play '{query}': {state['error']}"
    title = state.get("title") or query
    return f"Now playing: {title}"


@tool
def stop_music() -> str:
    """Stop music playback completely."""
    bridge = _bridge.get()
    bridge.music_command({"action": "stop", "t": time.time()})
    return "Music stopped."


@tool
def pause_music() -> str:
    """Pause music playback (resume_music continues from the same spot)."""
    _bridge.get().music_command({"action": "pause", "t": time.time()})
    return "Music paused."


@tool
def resume_music() -> str:
    """Resume paused music playback."""
    _bridge.get().music_command({"action": "resume", "t": time.time()})
    return "Music resumed."


@tool
def set_music_volume(percent: int = -1, change: int = 0) -> str:
    """Set or adjust the music volume.

    percent: absolute level 0-100 — "set the volume to 40" → percent=40.
    change:  relative step — "louder"/"increase the volume" → change=15,
             "quieter"/"a bit softer" → change=-15 (use ±25 for "much").
    Give exactly one of the two."""
    bridge = _bridge.get()
    if percent < 0 and change:
        state = bridge.get_music_state() or {}
        level = int(state.get("volume", 70)) + int(change)
    else:
        level = int(percent)
    level = max(0, min(100, level))
    bridge.music_command({"action": "volume", "level": level, "t": time.time()})
    return f"Volume set to {level}%."


def music_context() -> str:
    """One-line now-playing block for chat's prompt tail (dynamic zone —
    changes only when playback state changes, like household memory)."""
    bridge = _bridge.get()
    # music_node heartbeats at 1Hz while playing — a "playing" state with no
    # heartbeat for 5s means the player died; don't leave a ghost NOW PLAYING
    # in the prompt forever.
    state = bridge.get_music_state(max_playing_age_s=5.0)
    if not state or not state.get("playing"):
        return ""
    title = state.get("title", "music")
    paused = " (paused)" if state.get("paused") else ""
    volume = state.get("volume")
    vol = f" — volume {int(volume)}%" if volume is not None else ""
    return f"\n== NOW PLAYING ==\n{title}{paused}{vol}\n"
