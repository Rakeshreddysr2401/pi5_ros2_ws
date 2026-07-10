"""tools/reminders.py — plain reminders stay unchanged; new optional
telegram_recipient/telegram_report_back fields are additive (Gap 2)."""

import time
from dataclasses import asdict

import pytest

from langrobo_core.services import telegram as telegram_service
from langrobo_core.services.config import TelegramMember
from langrobo_core.tools import _relay_confirm
from langrobo_core.tools.reminders import Reminder, ReminderStore, set_reminder
from langrobo_core.tools.telegram import send_telegram_message


# ── Plain reminders: unaffected by the new fields ────────────────────────────

def test_plain_reminder_round_trips_unchanged(tmp_path):
    store = ReminderStore(path=str(tmp_path / "reminders.json"))
    r = store.add("check the oven", time.time() - 1)  # already due
    assert r.telegram_recipient is None
    assert r.telegram_report_back is False
    due = store.pop_due()
    assert [d.id for d in due] == [r.id]
    assert due[0].telegram_recipient is None


def test_old_json_row_without_new_keys_still_loads():
    old_row = {"id": 1, "text": "hi", "due": 123.0, "created": 100.0}
    r = Reminder(**old_row)
    assert r.telegram_recipient is None
    assert r.telegram_report_back is False
    # asdict must include the new keys with defaults for the persistence round-trip
    assert "telegram_recipient" in asdict(r)


# ── set_reminder tool: telegram_recipient is validated at creation time ─────

_MEMBERS = (TelegramMember(111, "Rakesh", "owner"),
           TelegramMember(222, "Mom", "family"))


class _FakeService:
    def __init__(self, members=_MEMBERS):
        self._members = members
        self.sent = []

    def configured(self):
        return True

    def member_names(self):
        return [m.name for m in self._members]

    def member_by_name(self, name):
        for m in self._members:
            if m.name.casefold() == name.casefold():
                return m
        return None

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return None

    def quiet_now(self):
        return False


@pytest.fixture
def fake_telegram(monkeypatch):
    fake = _FakeService()
    monkeypatch.setattr(telegram_service, "_instance", fake)
    return fake


@pytest.fixture(autouse=True)
def _isolated_reminder_store(tmp_path, monkeypatch):
    from langrobo_core.tools import reminders as reminders_module
    monkeypatch.setattr(reminders_module, "_store",
                        ReminderStore(path=str(tmp_path / "reminders.json")))
    yield


@pytest.fixture(autouse=True)
def _reset_relay_confirm():
    _relay_confirm.reset()
    yield
    _relay_confirm.reset()


def test_set_reminder_with_known_recipient_stores_it(fake_telegram):
    out = set_reminder.invoke({"text": "bring fruits", "in_minutes": 1,
                               "telegram_recipient": "Mom"})
    assert "Mom" in out
    from langrobo_core.tools.reminders import get_store
    [r] = get_store().active()
    assert r.telegram_recipient == "Mom"
    assert r.telegram_report_back is False


def test_set_reminder_unknown_recipient_errors_and_creates_nothing(fake_telegram):
    out = set_reminder.invoke({"text": "bring fruits", "in_minutes": 1,
                               "telegram_recipient": "Uncle"})
    assert "Error" in out
    from langrobo_core.tools.reminders import get_store
    assert get_store().active() == []


def test_set_reminder_telegram_recipient_without_service_errors():
    out = set_reminder.invoke({"text": "bring fruits", "in_minutes": 1,
                               "telegram_recipient": "Mom"})
    assert "Error: Telegram is not set up" in out


def test_set_reminder_plain_still_works_without_telegram(fake_telegram):
    out = set_reminder.invoke({"text": "check the oven", "in_minutes": 1})
    assert "Reminder" in out and "relaying" not in out


# ── Fire-time relay: channel="system" bypasses the D10 ask-back gate ────────

def test_system_channel_relay_bypasses_ask_back_gate(fake_telegram):
    result = send_telegram_message.invoke({
        "recipient": "Mom", "message": "Bring fruits home",
        "report_back": False,
        "state": {"channel": "system", "messages": []},
    })
    assert "delivered to Mom" in result
    assert fake_telegram.sent == [(222, "Bring fruits home")]


# ── Errand routing for scheduled relays ──────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_errand_store(tmp_path, monkeypatch):
    from langrobo_core.tools import errands as errands_module
    monkeypatch.setattr(errands_module, "_store",
                        errands_module.ErrandStore(path=str(tmp_path / "errands.json")))
    yield


def test_system_relay_errand_routes_reply_to_voice(fake_telegram):
    """A [SYSTEM]-scheduled relay's errand must be asked_via='voice' so the
    recipient's reply is announced aloud — 'system' would hit the
    telegram-forward branch and try to message the unresolvable 'the user'."""
    from langrobo_core.tools.errands import get_store as get_errand_store
    send_telegram_message.invoke({
        "recipient": "Mom", "message": "Bring fruits home",
        "report_back": True,
        "state": {"channel": "system", "messages": []},
    })
    [errand] = get_errand_store().pop_for_sender("Mom")
    assert errand.asked_via == "voice"


def test_quiet_hours_deferred_relay_still_records_errand(fake_telegram, monkeypatch):
    from langrobo_core.tools.errands import get_store as get_errand_store
    monkeypatch.setattr(fake_telegram, "quiet_now", lambda: True)
    deferred = []
    monkeypatch.setattr(fake_telegram, "defer",
                        lambda chat_id, text: deferred.append((chat_id, text)),
                        raising=False)
    result = send_telegram_message.invoke({
        "recipient": "Mom", "message": "Bring fruits home",
        "report_back": True,
        "state": {"channel": "system", "messages": []},
    })
    assert "quiet hours" in result
    assert deferred == [(222, "Bring fruits home")]
    [errand] = get_errand_store().pop_for_sender("Mom")
    assert errand.asked_via == "voice"
