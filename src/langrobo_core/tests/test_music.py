"""Music tool flow — play confirmation tokens and stale-state aging.

The Pi5 and Jetson clocks drift (~1.5s), so play_music must never compare the
Jetson's wall-clock stamp against ours. Confirmation matches on cmd_t — the
command's own `t` echoed back by music_node as an opaque token — and a 1Hz
heartbeat of a PREVIOUS song must never confirm a new request.
"""

import time

import pytest

from langrobo_core.tools import _bridge
from langrobo_core.tools.music import music_context, play_music


@pytest.fixture(autouse=True)
def _restore_bridge():
    """Leave whatever bridge other test modules installed untouched."""
    saved = _bridge._instance
    yield
    _bridge._instance = saved


class FakeMusicBridge:
    """Minimal bridge exposing only what the music tools touch."""

    def __init__(self):
        self.commands = []
        self._state = None
        self._seq = 0

    # tool-facing API (mirrors ROS2Bridge)
    def music_command(self, cmd):
        self.commands.append(cmd)

    def get_music_state(self, max_playing_age_s=None):
        if (self._state and self._state.get("playing")
                and max_playing_age_s is not None
                and self.age > max_playing_age_s):
            return None
        return self._state

    def get_music_state_seq(self):
        return self._seq

    # test helpers
    age = 0.0

    def feed(self, state):
        self._state = state
        self._seq += 1


def _with_bridge(bridge):
    _bridge._instance = None
    _bridge.init(bridge)
    return bridge


def test_play_music_confirms_on_matching_cmd_t():
    bridge = _with_bridge(FakeMusicBridge())
    # Confirm as soon as the state echoing OUR command's t arrives.
    orig_cmd = bridge.music_command

    def echo(cmd):
        orig_cmd(cmd)
        bridge.feed({"playing": True, "title": "Shape of You",
                     "cmd_t": cmd["t"], "stamp": time.time() - 99})  # stamp drift is irrelevant

    bridge.music_command = echo
    out = play_music.invoke({"query": "shape of you"})
    assert "Shape of You" in out


def test_play_music_ignores_previous_song_heartbeat():
    bridge = _with_bridge(FakeMusicBridge())
    # A heartbeat of the OLD song (different cmd_t) keeps arriving — it must
    # not confirm; with nothing else the tool reports no confirmation.
    import langrobo_core.tools.music as music_mod
    old_timeout = music_mod._PLAY_CONFIRM_TIMEOUT_S
    music_mod._PLAY_CONFIRM_TIMEOUT_S = 0.5
    try:
        bridge.feed({"playing": True, "title": "Old Song", "cmd_t": 12345.0})
        out = play_music.invoke({"query": "new song"})
        assert "no confirmation" in out
    finally:
        music_mod._PLAY_CONFIRM_TIMEOUT_S = old_timeout


def test_play_music_error_reported_honestly():
    bridge = _with_bridge(FakeMusicBridge())
    orig_cmd = bridge.music_command

    def echo(cmd):
        orig_cmd(cmd)
        bridge.feed({"playing": False, "error": "no results", "cmd_t": cmd["t"]})

    bridge.music_command = echo
    out = play_music.invoke({"query": "gibberish"})
    assert "no results" in out


def test_music_context_ages_out_dead_player():
    bridge = _with_bridge(FakeMusicBridge())
    bridge.feed({"playing": True, "title": "Zombie Track"})
    bridge.age = 0.0
    assert "Zombie Track" in music_context()
    bridge.age = 60.0   # heartbeat stopped a minute ago — player is dead
    assert music_context() == ""
