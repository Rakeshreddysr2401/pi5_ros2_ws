"""The errand that comes with a drive: "go to the box and tell me what is on it".

The drive runs in the background and the turn ends; arrival is a [SYSTEM]
turn minutes later. Before 2026-10-02 that turn knew only "I've arrived" and
the second half of the request was lost. Now the drive tools take `then`, and
movement.arrival_task_note() hands it to the arrival report: do it on
arrival, say it was not done on failure, drop it when a new command
cancelled the drive.
"""
import pytest

from langrobo_core.tools import movement as mv
from langrobo_core.tools import approach as ap
from langrobo_core.tools import _bridge
from langrobo_core.tools.approach import approach_described_object
from langrobo_core.tools.movement import navigate_to_pose

from test_movement import fake_twist  # noqa: F401 -- fixture
from test_object_memory import STATE, _grounded, robot, scripted  # noqa: F401 -- fixtures

BOX_TASK = "tell the user what is on the box"


@pytest.fixture(autouse=True)
def no_requester():
    mv._last_nav_requester = None
    yield
    mv._last_nav_requester = None


def test_no_errand_adds_nothing():
    mv.remember_requester(STATE)
    assert mv.arrival_task_note(True, "I've arrived at 'near the box'.") == ""


def test_on_arrival_the_errand_is_to_be_done_now():
    mv.remember_requester(STATE, BOX_TASK)
    note = mv.arrival_task_note(True, "I've arrived at 'near the box'.")
    assert BOX_TASK in note and "Do it now" in note


def test_a_failed_drive_says_the_errand_was_not_done():
    mv.remember_requester(STATE, BOX_TASK)
    note = mv.arrival_task_note(False, "Navigation to 'near the box' failed after retrying")
    assert BOX_TASK in note and "NOT done" in note


def test_a_cancelled_drive_drops_the_errand_quietly():
    """A new command stopped the drive: the user has moved on."""
    mv.remember_requester(STATE, BOX_TASK)
    assert mv.arrival_task_note(False, "Navigation to 'near the box' cancelled") == ""


def test_the_errand_is_used_once():
    mv.remember_requester(STATE, BOX_TASK)
    assert mv.arrival_task_note(True, "arrived")
    assert mv.arrival_task_note(True, "arrived") == ""


def test_a_new_drive_replaces_the_old_errand():
    mv.remember_requester(STATE, BOX_TASK)
    mv.remember_requester(STATE)                       # "no, just go to the chair"
    assert mv.arrival_task_note(True, "arrived") == ""


def test_the_channel_is_kept_with_the_errand():
    mv.remember_requester({"channel": "telegram", "sender_name": "Rakesh"}, BOX_TASK)
    req = mv.get_last_nav_requester()
    assert (req["channel"], req["sender"], req["then"]) == ("telegram", "Rakesh", BOX_TASK)


# ── the tools carry it ──────────────────────────────────────────────────────

def test_approach_stores_the_errand_when_the_drive_starts(robot, scripted, monkeypatch):
    turns, vlm = scripted
    monkeypatch.setattr(ap._recall, "ask", lambda *a, **k: {"ok": False, "why": "no_photos"})
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.5, 0.0)]
    approach_described_object.invoke({"description": "the box", "then": BOX_TASK,
                                      "state": dict(STATE)})
    assert robot.navs and mv.get_last_nav_requester()["then"] == BOX_TASK


def test_approach_that_never_drives_stores_no_errand(robot, scripted, monkeypatch):
    """Not found: no drive, so no arrival report -- the turn itself says so."""
    monkeypatch.setattr(ap._recall, "ask", lambda *a, **k: {"ok": False, "why": "no_photos"})
    approach_described_object.invoke({"description": "the box", "then": BOX_TASK,
                                      "state": dict(STATE)})
    assert not robot.navs and mv.get_last_nav_requester() is None


def test_navigate_to_pose_stores_the_errand(robot, monkeypatch):
    robot.add_known_location("kitchen", 2.0, 1.0, 0.0)
    monkeypatch.setattr(robot, "get_known_locations", lambda: {"kitchen": (2.0, 1.0, 0.0)})
    navigate_to_pose.invoke({"location": "kitchen", "then": "send Rakesh a photo of the table",
                             "state": dict(STATE)})
    assert robot.navs and mv.get_last_nav_requester()["then"] == "send Rakesh a photo of the table"


# ── the arrival check: is it really there? ─────────────────────────────────

def test_approach_remembers_what_it_drove_to_and_who_asked(robot, scripted, monkeypatch):
    turns, vlm = scripted
    monkeypatch.setattr(ap._recall, "ask", lambda *a, **k: {"ok": False, "why": "no_photos"})
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.5, 0.0)]
    approach_described_object.invoke({"description": "the blue box", "state": dict(
        STATE, channel="telegram", sender_name="Rakesh", sender_role="owner")})
    req = mv.get_last_nav_requester()
    assert (req["target"], req["sender"], req["role"]) == ("the blue box", "Rakesh", "owner")


def test_arrival_sees_it_and_measures_it(robot, scripted):
    turns, vlm = scripted
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(0.5, 0.0, depth=0.5)]
    note = ap.arrival_check("the blue box")
    assert note == " I can see the blue box in front of me, about 0.5 m away."


def test_arrival_does_not_see_it_and_says_so(robot, scripted):
    note = ap.arrival_check("the blue box")            # the VLM finds nothing
    assert "can't see the blue box in front of me" in note


def test_arrival_check_never_holds_up_the_report(robot, scripted, monkeypatch):
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (None, None))
    assert ap.arrival_check("the blue box") == ""      # camera down: no claim either way

    def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", {"stamp": (1, 2)}))
    monkeypatch.setattr(ap, "_vlm_locate", boom)
    assert ap.arrival_check("the blue box") == ""


def test_a_place_drive_has_no_object_to_check(robot):
    robot.add_known_location("kitchen", 2.0, 1.0, 0.0)
    navigate_to_pose.invoke({"location": "kitchen", "state": dict(STATE)})
    assert mv.get_last_nav_requester()["target"] is None
