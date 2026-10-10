"""follow_person — "follow me" / "come to me" (tools/follow.py).

The properties: off by default (no schema, prompts unchanged); the same
guards as every drive (role, teleop MANUAL); refused over Telegram, where
"me" is nobody the camera can see; an honest first answer from the Jetson's
first status ("nobody" is said now, not promised away); and the requester is
remembered so the end report reaches whoever asked, with "then" kept only
for "come".
"""

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import FOLLOW_ENABLED, FOLLOW_TOOLS, NAVIGATE_TOOLS, _bridge
from langrobo_core.tools import follow as fl
import langrobo_core.tools.movement as mv
from langrobo_core.tools.follow import follow_person

VOICE = {"channel": "voice", "sender_name": "voice", "messages": []}


class FollowBridge(StubBridge):
    def __init__(self, answer):
        super().__init__()
        self.answer, self.calls = answer, []

    def start_follow(self, mode="follow"):
        self.calls.append(mode)
        return self.answer


@pytest.fixture(autouse=True)
def _auto(monkeypatch):
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: False)
    yield
    _bridge._instance = None
    _bridge.init(StubBridge())


def _use(answer):
    b = FollowBridge(answer)
    _bridge._instance = None
    _bridge.init(b)
    return b


def test_bound_only_with_the_switch():
    assert (follow_person in NAVIGATE_TOOLS) == FOLLOW_ENABLED
    assert FOLLOW_TOOLS == ([follow_person] if FOLLOW_ENABLED else [])


def test_switch_parsing(monkeypatch):
    import importlib
    for val, on in (("1", True), ("on", True), ("0", False), ("", False), ("off", False)):
        monkeypatch.setenv("LANGROBO_FOLLOW", val)
        assert importlib.reload(fl).FOLLOW_ENABLED is on, val
    monkeypatch.delenv("LANGROBO_FOLLOW")
    importlib.reload(fl)


def test_starts_and_says_so():
    b = _use({"ok": True, "result": "following", "target": 3, "dist": 1.8})
    out = follow_person.invoke({"state": VOICE})
    assert b.calls == ["follow"] and "Following you now" in out


def test_come_mode_keeps_then(monkeypatch):
    seen = {}
    monkeypatch.setattr(fl, "remember_requester", lambda s, then="": seen.update(then=then))
    b = _use({"ok": True, "result": "following"})
    out = follow_person.invoke({"state": VOICE, "mode": "come", "then": "tell the user the time"})
    assert b.calls == ["come"] and "Coming to you" in out
    assert seen["then"] == "tell the user the time"
    follow_person.invoke({"state": VOICE, "mode": "follow", "then": "something"})
    assert seen["then"] == ""          # a follow has no arrival to do it on


def test_nobody_in_view_is_said_now():
    _use({"ok": False, "result": "nobody", "why": "no one in view to follow"})
    out = follow_person.invoke({"state": VOICE})
    assert "can't see anyone" in out and "Do not turn or search" in out


def test_jetson_not_running():
    out = follow_person.invoke({"state": VOICE})          # the plain StubBridge
    assert "not available" in out


def test_manual_refuses_without_starting(monkeypatch):
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: True)
    b = _use({"ok": True, "result": "following"})
    out = follow_person.invoke({"state": VOICE})
    assert "MANUAL" in out and b.calls == []


@pytest.mark.parametrize("role", ["family", "guest"])
def test_roles_without_move_refused(role):
    b = _use({"ok": True, "result": "following"})
    st = {"channel": "telegram", "sender_name": "Mom", "sender_role": role, "messages": []}
    out = follow_person.invoke({"state": st})
    assert "not allowed to move" in out and b.calls == []


def test_telegram_owner_refused_me_is_not_visible():
    b = _use({"ok": True, "result": "following"})
    st = {"channel": "telegram", "sender_name": "Rakesh", "sender_role": "owner", "messages": []}
    out = follow_person.invoke({"state": st})
    assert "over Telegram" in out and b.calls == []


def test_other_failures_are_reported():
    _use({"ok": False, "result": "pose unsure", "why": "LiDAR odometry unhealthy"})
    out = follow_person.invoke({"state": VOICE})
    assert "LiDAR odometry unhealthy" in out
