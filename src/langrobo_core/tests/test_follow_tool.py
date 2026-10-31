"""follow_person — "follow me" / "come to me" (tools/follow.py).

The properties: off by default (no schema, prompts unchanged); the same
guards as every drive (role, teleop MANUAL); over Telegram it follows whoever
is in front of the camera and sends the asker a photo of them; an honest first answer from the Jetson's
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

    def start_follow(self, mode="follow", announce=False, who="you", photo_on_start=False):
        self.calls.append(mode)
        self.announce, self.who, self.photo_on_start = announce, who, photo_on_start
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


def test_nobody_in_view_turns_to_find_them():
    # 2026-10-13: follow_node turns to find someone; the tool says so, and the
    # bridge is told to announce the find on a voice turn only
    b = _use({"ok": True, "result": "finding"})
    out = follow_person.invoke({"state": VOICE})
    assert "turning around to look for you" in out and "follow you" in out
    assert b.announce is True
    out = follow_person.invoke({"state": VOICE, "mode": "come"})
    assert "come to you" in out


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


TELEGRAM_OWNER = {"channel": "telegram", "sender_name": "Rakesh", "sender_role": "owner",
                  "messages": []}


def test_telegram_follow_me_follows_and_sends_who():
    """Owner, 2026-10-31: standing by the robot with only the phone (no
    speaker/mic) -- "follow me" on Telegram follows whoever is in front,
    and the photo of who it follows goes to the sender."""
    b = _use({"ok": True, "result": "following"})
    out = follow_person.invoke({"state": TELEGRAM_OWNER})
    assert b.calls == ["follow"] and b.photo_on_start is True and b.announce is False
    assert "starting to follow them" in out and "photo" in out and "stop" in out


def test_telegram_follow_me_turns_to_find_someone():
    b = _use({"ok": True, "result": "finding"})
    out = follow_person.invoke({"state": TELEGRAM_OWNER})
    assert b.calls == ["follow"] and "turning around to find someone" in out


def test_telegram_follow_me_with_nobody_found_asks_to_stand_in_front():
    _use({"ok": False, "result": "nobody"})
    out = follow_person.invoke({"state": TELEGRAM_OWNER})
    assert "NOT started" in out and "stand in front" in out


def test_photo_on_start_only_for_a_telegram_follow():
    b = _use({"ok": True, "result": "following"})
    follow_person.invoke({"state": VOICE})
    assert b.photo_on_start is False
    follow_person.invoke({"state": TELEGRAM_OWNER, "mode": "come"})
    assert b.photo_on_start is False


def test_telegram_come_goes_to_the_person_in_the_room(monkeypatch):
    """2026-10-20: "find my mom, go near her and tell her to bring water" was
    refused four times, and the model said "I am looking for her" each time."""
    seen = {}
    monkeypatch.setattr(fl, "remember_requester", lambda s, then="": seen.update(then=then))
    b = _use({"ok": True, "result": "finding"})
    out = follow_person.invoke({"state": TELEGRAM_OWNER, "mode": "come",
                                "then": "say out loud to her: bring water"})
    assert b.calls == ["come"] and b.who == "the person I found" and b.announce is False
    assert "whoever I find" in out and "photo" in out
    assert seen["then"] == "say out loud to her: bring water"


def test_telegram_come_with_nobody_in_view_does_not_ask_to_stand_in_front():
    _use({"ok": False, "result": "nobody"})
    out = follow_person.invoke({"state": TELEGRAM_OWNER, "mode": "come"})
    assert "can't see anyone in the room" in out and "stand in front" not in out


def test_other_failures_are_reported():
    _use({"ok": False, "result": "pose unsure", "why": "LiDAR odometry unhealthy"})
    out = follow_person.invoke({"state": VOICE})
    assert "LiDAR odometry unhealthy" in out
