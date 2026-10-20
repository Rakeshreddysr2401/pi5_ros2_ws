"""The Telegram chat of 2026-10-20, as tests. No robot, no LLM.

  - "go near the dining table" held the brain for 2 min (the search ran inside
    the tool call); the door's arrival report waited behind it.
  - asked again for the same table, the drive was cancelled and restarted;
    "send me a pic once you arrived" restarted it a third time: two arrivals.
  - "I can't see the dining table" in the report reached the phone as "I have
    arrived at the dining table".
  - "tell her loudly" had no way to reach the speaker.
"""

import threading
import time

import pytest

from langrobo_core.services import notices
from langrobo_core.tools import CHAT_TOOLS, NAVIGATE_TOOLS, _bridge
from langrobo_core.tools import approach as ap
from langrobo_core.tools import movement as mv
from langrobo_core.tools import telegram as tg
from langrobo_core.tools.approach import approach_described_object
from langrobo_core.tools.room_voice import say_in_room

from test_movement import fake_twist  # noqa: F401 -- fixture
from test_object_memory import _grounded, robot, scripted  # noqa: F401 -- fixtures

TELEGRAM = {"channel": "telegram", "sender_name": "Rakesh", "sender_role": "owner",
            "messages": []}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    sent = []
    notices.set_sink(lambda text, req, report="", photo=False: sent.append(
        {"text": text, "req": req, "report": report, "photo": photo}))
    monkeypatch.setattr(ap._recall, "ask", lambda *a, **k: {"ok": False, "why": "no_photos"})
    mv._last_nav_requester = None
    ap._job = None
    tg._pending_photo = None
    yield sent
    notices.set_sink(None)
    if ap._job is not None:
        ap._job["thread"].join(timeout=5)
    ap._job = None
    mv._last_nav_requester = None


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ── the search runs in the background ───────────────────────────────────────

def test_a_slow_search_returns_at_once_and_reports_by_notice(robot, scripted, monkeypatch, clean):
    turns, vlm = scripted
    gate = threading.Event()
    real = ap._vlm_locate
    monkeypatch.setattr(ap, "_vlm_locate", lambda f, d: (gate.wait(5), real(f, d))[1])
    monkeypatch.setattr(ap, "_QUICK_S", 0.05)
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(2.0, 0.0, depth=2.9)]
    t0 = time.time()
    out = approach_described_object.invoke({"description": "the dining table",
                                            "state": dict(TELEGRAM)})
    assert time.time() - t0 < 2.0
    assert "Looking for the dining table now" in out and "do NOT say you have found" in out
    assert ap.search_active()
    gate.set()
    assert _wait(lambda: clean)
    note = clean[0]
    assert "I can see the dining table" in note["text"] and "about 2.9 m" in note["text"]
    assert "MOVED" not in note["text"]                     # the model's note stays in the report
    assert note["report"].startswith("[SYSTEM] The search for the dining table finished")
    assert note["req"]["channel"] == "telegram" and note["req"]["sender"] == "Rakesh"
    assert robot.navs and robot.navs[0][3] == "near the dining table"


def test_a_quick_answer_is_still_the_tools_own_result(robot, scripted, clean):
    turns, vlm = scripted
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.5, 0.0)]
    out = approach_described_object.invoke({"description": "the box", "state": dict(TELEGRAM)})
    assert "I can see the box" in out and "MOVED" in out
    assert clean == []                                     # nothing sent twice


def test_without_a_sink_it_blocks_as_before(robot, scripted):
    notices.set_sink(None)
    turns, vlm = scripted
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.5, 0.0)]
    out = approach_described_object.invoke({"description": "the box", "state": dict(TELEGRAM)})
    assert "I can see the box" in out and ap._job is None


def test_not_found_in_the_background_is_reported_and_drops_a_held_photo(robot, scripted,
                                                                        monkeypatch, clean):
    monkeypatch.setattr(ap, "_QUICK_S", 0.0)
    gate = threading.Event()
    monkeypatch.setattr(ap, "_vlm_locate", lambda f, d: gate.wait(5) and None)
    approach_described_object.invoke({"description": "the red ball", "state": dict(TELEGRAM)})
    tg._pending_photo = {"chat_id": 1, "name": "Rakesh", "caption": "", "sender": "Rakesh",
                         "at": time.time()}
    gate.set()
    assert _wait(lambda: clean)
    assert "couldn't spot the red ball" in clean[0]["text"]
    assert tg._pending_photo is None


# ── the same object again: add to the drive, never restart it ───────────────

def test_asking_again_while_driving_adds_the_errand(robot, clean, monkeypatch):
    mv.remember_requester(TELEGRAM, target="the dining table")
    monkeypatch.setattr(robot, "navigation_state", lambda: {
        "active": True, "goal": {"x": 1.0, "y": 0.0, "label": "near the dining table",
                                 "since": time.time()}, "last": None})
    out = approach_described_object.invoke({
        "description": "the dining table", "then": "send Rakesh a photo",
        "state": dict(TELEGRAM)})
    assert "already on my way to the dining table" in out and "Nothing was restarted" in out
    assert robot.navs == []                                # no second goal
    assert mv.get_last_nav_requester()["then"] == "send Rakesh a photo"


def test_asking_again_while_searching_carries_the_errand_to_the_drive(robot, scripted,
                                                                      monkeypatch, clean):
    turns, vlm = scripted
    gate = threading.Event()
    real = ap._vlm_locate
    monkeypatch.setattr(ap, "_vlm_locate", lambda f, d: (gate.wait(5), real(f, d))[1])
    monkeypatch.setattr(ap, "_QUICK_S", 0.0)
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(2.0, 0.0)]
    approach_described_object.invoke({"description": "the dining table", "state": dict(TELEGRAM)})
    out = approach_described_object.invoke({"description": "dining table",
                                            "then": "send a photo", "state": dict(TELEGRAM)})
    assert "already looking for the dining table" in out
    gate.set()
    assert _wait(lambda: clean)
    assert len(robot.navs) == 1
    assert mv.get_last_nav_requester()["then"] == "send a photo"


def test_something_else_supersedes_the_old_search(robot, scripted, monkeypatch, clean):
    turns, vlm = scripted
    gate = threading.Event()
    seen = []

    def vlm_fn(f, d):
        seen.append(d)
        if d == "the door":
            gate.wait(5)
            return (448.0, 250.0, None)
        return (448.0, 250.0, None)
    monkeypatch.setattr(ap, "_vlm_locate", vlm_fn)
    monkeypatch.setattr(ap, "_QUICK_S", 0.0)
    robot.replies = [_grounded(1.0, 0.0), _grounded(2.0, 0.0)]
    approach_described_object.invoke({"description": "the door", "state": dict(TELEGRAM)})
    approach_described_object.invoke({"description": "the chair", "state": dict(TELEGRAM)})
    gate.set()
    assert _wait(lambda: ap._job["done"].is_set(), 5)
    assert [n[3] for n in robot.navs] == ["near the chair"]   # the door never drove
    assert [c["text"] for c in clean if "door" in c["text"]] == []


# ── reports as said to a person ─────────────────────────────────────────────

@pytest.mark.parametrize("ok, msg, want", [
    (True, "I've arrived at 'near the dining table'.", "I've arrived near the dining table."),
    (True, "I've arrived at 'kitchen'.", "I've arrived at the kitchen."),
    (False, "Navigation to 'near the box' failed after retrying -- no path.",
     "I couldn't get near the box: no path."),
    (False, "Navigation to 'kitchen' failed — path may be blocked.",
     "I couldn't get to the kitchen. path may be blocked."),
    (False, "Navigation goal rejected by Nav2", "I couldn't get there: Navigation goal rejected by Nav2"),
    (True, "I've come to the person I found (1.0 m).", "I've come to the person I found (1.0 m)."),
])
def test_report_sentence(ok, msg, want):
    assert mv.report_sentence(ok, msg) == want


def test_a_plain_report_is_a_notice_and_an_errand_is_a_turn():
    mv.remember_requester(TELEGRAM, target="the dining table")
    rep = mv.nav_report(True, "I've arrived at 'near the dining table'.")
    assert not rep["errand"] and rep["notice"] == "I've arrived near the dining table."
    assert rep["check"][0] == "the dining table"
    mv.remember_requester(TELEGRAM, then="say out loud: bring water")
    rep = mv.nav_report(True, "I've come to the person I found.")
    assert rep["errand"] and "already been sent" in rep["text"] and "bring water" in rep["text"]


def test_a_cancelled_drive_is_marked():
    mv.remember_requester(TELEGRAM)
    assert mv.nav_report(False, "Navigation to 'near the door' cancelled")["cancelled"]


def test_add_errand_joins_without_repeating():
    mv.remember_requester(TELEGRAM, then="tell me what is on it")
    assert mv.add_errand("send a photo") and mv.add_errand("send a photo")
    assert mv.get_last_nav_requester()["then"] == "tell me what is on it; then send a photo"


# ── a Telegram sender's words, said in the room ─────────────────────────────

class Speaker:
    def __init__(self):
        self.said = []

    def publish_speech(self, text):
        self.said.append(text)


@pytest.fixture
def speaker(monkeypatch):
    s = Speaker()
    monkeypatch.setattr(_bridge, "_instance", s)
    yield s
    _bridge._instance = None


def test_say_in_room_speaks_for_a_telegram_sender(speaker):
    out = say_in_room.invoke({"message": "Rakesh says: please bring water.",
                              "state": dict(TELEGRAM)})
    assert speaker.said == ["Rakesh says: please bring water."] and "Said out loud" in out


def test_say_in_room_on_a_quiet_errand_turn(speaker):
    st = {"channel": "system", "sender_name": "Rakesh", "sender_role": "owner"}
    say_in_room.invoke({"message": "Bring water, please.", "state": st})
    assert speaker.said == ["Bring water, please."]


def test_say_in_room_is_not_used_in_a_spoken_conversation(speaker):
    out = say_in_room.invoke({"message": "hello", "state": {"channel": "voice"}})
    assert speaker.said == [] and "already said aloud" in out


def test_say_in_room_needs_the_relay_capability(speaker):
    st = dict(TELEGRAM, sender_role="guest", sender_name="Guest")
    out = say_in_room.invoke({"message": "hello", "state": st})
    assert speaker.said == [] and "NOT said" in out


def test_tool_sets():
    assert say_in_room in CHAT_TOOLS and say_in_room not in NAVIGATE_TOOLS


def test_a_photo_asked_for_while_searching_waits_for_the_arrival(monkeypatch):
    class Svc:
        def configured(self):
            return True

        def member_by_name(self, n):
            return type("M", (), {"chat_id": 7, "name": "Rakesh"})()

        def member_names(self):
            return ["Rakesh"]

    from langrobo_core.services import telegram as tsvc
    monkeypatch.setattr(tsvc, "get", lambda: Svc())
    monkeypatch.setattr(ap, "search_active", lambda: True)
    out = tg.send_telegram_photo.invoke({"recipient": "Rakesh", "caption": "here",
                                         "state": dict(TELEGRAM)})
    assert "Not sent yet" in out and tg._pending_photo["chat_id"] == 7
