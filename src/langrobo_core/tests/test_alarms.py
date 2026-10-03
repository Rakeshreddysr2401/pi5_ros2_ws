"""services/alarms.py + tools/reminders.py: when things are due, what rings, what is said."""
from datetime import datetime

import pytest

from langrobo_core.services import alarms
from langrobo_core.tools import reminders as R

# Saturday 2026-10-03 20:00 local
NOW = datetime(2026, 10, 3, 20, 0, 0).timestamp()


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_STATE_DIR", str(tmp_path))


def at(h, m=0, day=3):
    return datetime(2026, 10, day, h, m).timestamp()


def test_parse_due():
    assert alarms.parse_due(10, now=NOW) == NOW + 600
    assert alarms.parse_due(1.5, now=NOW) == NOW + 90
    assert alarms.parse_due(at="21:30", now=NOW) == at(21, 30)
    assert alarms.parse_due(at="6:30 am", now=NOW) == at(6, 30, day=4)     # passed today -> tomorrow
    assert alarms.parse_due(at="7 pm", now=NOW) == at(19, 0, day=4)
    assert alarms.parse_due(at="12 am", now=NOW) == at(0, 0, day=4)
    assert alarms.parse_due(at="2026-10-05 07:30", now=NOW) == at(7, 30, day=5)
    for bad in ("", "soon", "25:00", "13 pm"):
        with pytest.raises(ValueError):
            alarms.parse_due(at=bad, now=NOW)


def test_spoken_when():
    assert alarms.spoken_when(NOW + 45, NOW) == "in 45 seconds"
    assert alarms.spoken_when(NOW + 600, NOW) == "in 10 minutes"
    assert alarms.spoken_when(at(21, 30), NOW) == "at 9:30 PM"
    assert alarms.spoken_when(at(6, 30, day=4), NOW) == "at 6:30 AM tomorrow"


def test_add_pending_cancel():
    alarms.add("timer", NOW + 300, "tea", now=NOW)
    alarms.add("alarm", at(6, 30, day=4), now=NOW)
    alarms.add("reminder", at(21, 0), "take medicine", now=NOW)
    assert [i["kind"] for i in alarms.pending(NOW)] == ["timer", "reminder", "alarm"]
    assert [i["label"] for i in alarms.cancel("tea")] == ["tea"]
    assert [i["kind"] for i in alarms.cancel("alarms")] == ["alarm"]
    assert alarms.cancel("nothing like this") == []
    assert len(alarms.cancel("all")) == 1 and alarms.pending(NOW) == []


def test_pop_due_rings_once_and_repeats_daily():
    alarms.add("timer", NOW + 60, "tea", now=NOW)
    alarms.add("alarm", at(20, 1), "walk", daily=True, now=NOW)
    assert alarms.pop_due(NOW) == []
    rang = alarms.pop_due(NOW + 61)
    assert sorted(i["label"] for i in rang) == ["tea", "walk"]
    assert alarms.pop_due(NOW + 62) == []                       # never twice
    left = alarms.pending(NOW + 62)
    assert len(left) == 1 and left[0]["due"] == at(20, 1, day=4)   # the daily one moved a day


def test_too_late_is_dropped_not_rung():
    alarms.add("reminder", NOW + 60, "old", now=NOW)
    assert alarms.pop_due(NOW + 60 + alarms.LATE_LIMIT_S + 5) == []
    assert alarms.pending(NOW + 60 + alarms.LATE_LIMIT_S + 5) == []


def test_announcement_words():
    t = {"kind": "timer", "due": NOW, "label": "tea"}
    assert alarms.announcement(t, NOW) == "Your tea timer is done."
    a = {"kind": "alarm", "due": at(6, 30, day=4), "label": ""}
    assert alarms.announcement(a, at(6, 30, day=4)) == "It's 6:30 AM. Time to wake up."
    r = {"kind": "reminder", "due": NOW, "label": "take medicine"}
    assert alarms.announcement(r, NOW + 600).endswith("Sorry, I was offline when it was due.")


def test_tools(monkeypatch):
    out = R.set_reminder.func(kind="timer", minutes=10, label="tea")
    assert out.startswith("Set: timer for tea, in 10 minutes") or out.startswith("Set: timer for tea, in 9 minutes")
    assert "Could not set it" in R.set_reminder.func(kind="alarm", at="soon")
    assert "tea" in R.reminders.func()
    assert "Ask which one" in R.reminders.func(action="cancel")
    assert R.reminders.func(action="cancel", which="tea").startswith("Cancelled: timer for tea")
    assert R.reminders.func() == "There are no timers, alarms or reminders set."


def test_tools_are_on_chat():
    from langrobo_core.tools import CHAT_TOOLS
    assert {"set_reminder", "reminders"} <= {t.name for t in CHAT_TOOLS}
