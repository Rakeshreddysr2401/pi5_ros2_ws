"""utils/addressing.py: room talk must not reach the world (2026-10-13:
"Amma, dinner is ready, come and eat" sent a Telegram message)."""

from langrobo_core.tools.memory import memory
from langrobo_core.tools.telegram import send_telegram_message
from langrobo_core.utils import addressing as a


def test_who_counts_as_addressed():
    assert a.addressed("Keeno, can you move forward?", "voice", None, 100.0)
    assert a.addressed("Keeno what time is it", "voice", None, 100.0)
    assert not a.addressed("Amma, dinner is ready", "voice", None, 100.0)
    assert a.addressed("yes send it", "voice", 90.0, 100.0)            # answering the robot
    assert not a.addressed("yes send it", "voice", 100.0 - a.FOLLOW_UP_S - 1, 100.0)
    assert a.addressed("anything", "telegram", None, 100.0)
    assert a.addressed("[SYSTEM] arrived", "system", None, 100.0)


def test_unset_means_addressed_so_studio_and_the_twin_are_unchanged():
    assert not a.not_addressed({})
    assert not a.not_addressed(None)
    assert a.not_addressed({"addressed": False})


def test_room_talk_sends_nothing():
    out = send_telegram_message.func("Amma", "Dinner is ready", state={"addressed": False,
                                                                       "channel": "voice"})
    assert out.startswith("NOT done") and "[silent]" in out


def test_room_talk_remembers_and_forgets_nothing_but_may_recall(tmp_path, monkeypatch):
    from langrobo_core.services import memory as store
    calls = []
    monkeypatch.setattr(store, "remember", lambda *x, **k: calls.append("remember"))
    monkeypatch.setattr(store, "forget", lambda *x, **k: calls.append("forget"))
    monkeypatch.setattr(store, "recall", lambda *x, **k: [])
    st = {"addressed": False}
    assert memory.func("remember", "I'll call back after the meeting", state=st).startswith("NOT done")
    assert memory.func("forget", "the meeting", state=st).startswith("NOT done")
    assert memory.func("recall", "the meeting", state=st) == "Nothing remembered about that."
    assert calls == []
