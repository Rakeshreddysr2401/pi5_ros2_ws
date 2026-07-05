"""announce_at_home — the Telegram→speaker path. Pure-zone tests.

The tool's job: capability gate, quiet-hours refusal with explicit override,
voice-caller short-circuit, and handing the announcement to the [SYSTEM]
queue via the bridge (StubBridge records it).
"""

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge as bridge_module
from langrobo_core.tools.announce import announce_at_home


@pytest.fixture()
def stub_bridge(monkeypatch):
    bridge = StubBridge()
    monkeypatch.setattr(bridge_module, "_instance", bridge)
    return bridge


def _call(state: dict, message: str = "Rakesh says he'll be late",
          override: bool = False) -> str:
    return announce_at_home.func(message=message, state=state,
                                 override_quiet_hours=override)


def test_owner_announcement_enqueues_system_turn(stub_bridge):
    out = _call({"channel": "telegram", "sender_name": "Rakesh",
                 "sender_role": "owner"})
    assert "aloud" in out.lower()
    assert len(stub_bridge.system_turns) == 1
    turn = stub_bridge.system_turns[0]
    assert turn.startswith("[SYSTEM]")
    assert "Rakesh says he'll be late" in turn


def test_guest_is_refused(stub_bridge):
    out = _call({"channel": "telegram", "sender_name": "Stranger",
                 "sender_role": "guest"})
    assert "Permission denied" in out
    assert stub_bridge.system_turns == []


def test_voice_caller_is_redirected(stub_bridge):
    # A voice reply already IS speech — the tool must not double-speak.
    out = _call({"channel": "voice"})
    assert "just say it in your reply" in out
    assert stub_bridge.system_turns == []


def test_quiet_hours_refuse_then_override(stub_bridge, monkeypatch):
    from langrobo_core.services import telegram as telegram_service

    class QuietTelegram:
        def quiet_now(self):
            return True

    monkeypatch.setattr(telegram_service, "_instance", QuietTelegram())
    state = {"channel": "telegram", "sender_name": "Rakesh",
             "sender_role": "owner"}
    out = _call(state)
    assert "quiet hours" in out.lower() and "did NOT announce" in out
    assert stub_bridge.system_turns == []
    # The sender insists — override goes through.
    out = _call(state, override=True)
    assert len(stub_bridge.system_turns) == 1


def test_empty_message_is_rejected(stub_bridge):
    out = _call({"channel": "telegram", "sender_name": "Rakesh",
                 "sender_role": "owner"}, message="   ")
    assert "nothing to announce" in out.lower()
    assert stub_bridge.system_turns == []
