"""Object memory and the checker (rover repo INTELLIGENCE_PLAN.md B3).

The owner's case: the robot saw a bottle from position 1; at position 2,
"go near the bottle" should turn to face where it was, check it is still
there ("sometimes the bottle may be removed by someone"), and go -- or say it
has gone and search.

Off-robot: StubBridge plus scripted camera / VLM / Jetson replies.
"""

import math

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.services import object_memory as om
from langrobo_core.tools import _bridge
from langrobo_core.tools import approach as ap
from langrobo_core.tools import locate as lo
from langrobo_core.tools import movement as mv
from langrobo_core.tools.approach import approach_described_object
from langrobo_core.tools.locate import locate_object
from langrobo_core.tools.memory import recall_object

from test_movement import fake_twist  # noqa: F401 -- fixture

EPOCH = 1790422385.619
STATE = {"channel": "voice", "sender_name": "voice", "messages": []}


# ── the store ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query, desc, ok", [
    ("bottle", "the orange bottle", True),
    ("the orange bottle", "orange bottle on the floor", True),
    ("my bottles", "the bottle", True),
    ("the red bottle", "the orange bottle", False),        # different colours
    ("the chair", "the orange bottle", False),
    ("", "the orange bottle", False),
])
def test_match(query, desc, ok):
    assert (om.match_score(query, desc) >= 0.5) is ok


def test_a_nearby_sighting_of_the_same_thing_updates_it():
    a = om.remember("the orange bottle", 1.0, 2.0, (0, 0, 0), EPOCH, when=100.0)
    b = om.remember("orange bottle", 1.1, 2.1, (0.5, 0, 90), EPOCH, when=200.0)
    assert b["id"] == a["id"] and b["sightings"] == 2 and (b["x"], b["y"]) == (1.1, 2.1)
    assert len(om.recall("", EPOCH, now=210.0)) == 1


def test_a_far_sighting_is_another_entry():
    om.remember("the orange bottle", 1.0, 2.0, None, EPOCH, when=100.0)
    om.remember("the orange bottle", 3.0, 2.0, None, EPOCH, when=200.0)
    got = om.recall("bottle", EPOCH, now=210.0)
    assert [e["x"] for e in got] == [3.0, 1.0], "best match, then most recent first"


def test_another_odom_origin_remembers_nothing():
    """A power cycle or pose restart moves the origin: those numbers point at
    nothing, and the owner wants a fresh start anyway."""
    om.remember("the orange bottle", 1.0, 2.0, None, EPOCH)
    assert om.recall("bottle", EPOCH + 1.0) == []


def test_old_entries_are_not_served():
    om.remember("the orange bottle", 1.0, 2.0, None, EPOCH, when=0.0)
    assert om.recall("bottle", EPOCH, now=om.MAX_AGE_S + 1.0) == []


def test_forget():
    e = om.remember("the orange bottle", 1.0, 2.0, None, EPOCH)
    assert om.forget(e["id"], EPOCH) and om.recall("bottle", EPOCH) == []


def test_relative_is_from_the_robot_now():
    e = {"x": 1.0, "y": 1.0}
    d, b = om.relative(e, (0.0, 0.0, 0.0))
    assert d == pytest.approx(math.sqrt(2)) and b == pytest.approx(45.0)
    assert om.relative(e, (0.0, 0.0, 90.0))[1] == pytest.approx(-45.0)   # now on the right
    assert om.relative(e, None) is None


# ── recall_object ───────────────────────────────────────────────────────────

class Robot(StubBridge):
    def __init__(self, pose=(0.0, 0.0, 0.0)):
        super().__init__()
        self.pose, self.origin_epoch = pose, EPOCH
        self.navs, self.queries = [], []
        self.replies = []

    def ground_pixel(self, u, v, timeout=4.0, stamp=None, box=None):
        self.queries.append((u, v))
        return self.replies.pop(0)

    def start_nav_to_pose(self, x, y, yaw_deg, label=""):
        self.navs.append((x, y, yaw_deg, label))


@pytest.fixture
def robot(monkeypatch):
    r = Robot()
    monkeypatch.setattr(_bridge, "_instance", r)
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: False)
    yield r
    _bridge._instance = None
    _bridge.init(StubBridge())


def _grounded(x, y, depth=1.2):
    return {"ok": True, "at_capture": True, "region": True, "depth_m": depth,
            "object": {"x": x, "y": y}, "goal": {"x": x - 0.45, "y": y, "yaw": 0.0},
            "relative": {"forward_m": depth, "left_m": 0.0, "bearing_deg": 0.0}}


def test_recall_answers_from_where_the_robot_is_now(robot):
    om.remember("the orange bottle", 1.0, 1.0, (0, 0, 0), EPOCH)
    out = recall_object.invoke({"description": "bottle"})
    assert "1.4 m" in out and "left" in out and "haven't checked" in out
    robot.pose = (0.0, 0.0, 90.0)                       # turned: now it is on the right
    assert "right" in recall_object.invoke({"description": "bottle"})


def test_recall_admits_it_remembers_nothing(robot):
    assert "don't remember" in recall_object.invoke({"description": "bottle"})


def test_recall_everything(robot):
    om.remember("the orange bottle", 1.0, 1.0, None, EPOCH)
    om.remember("the wooden chair", -1.0, 2.0, None, EPOCH)
    out = recall_object.invoke({"description": ""})
    assert "orange bottle" in out and "wooden chair" in out


# ── locate_object writes memory ─────────────────────────────────────────────

def test_locate_remembers_what_it_measured(robot, monkeypatch):
    monkeypatch.setattr(lo, "_capture", lambda b, settle_s=2.5: (b"jpeg", {"stamp": None, "pose": (0, 0, 0)}))
    monkeypatch.setattr(lo, "_vlm_locate", lambda f, d: (100.0, 50.0, None))
    robot.replies = [_grounded(1.2, 0.3)]
    locate_object.invoke({"description": "the orange bottle"})
    (e,) = om.recall("bottle", EPOCH)
    assert (e["x"], e["y"]) == (1.2, 0.3) and e["seen_from"] == [0, 0, 0]


def test_locate_not_in_view_says_where_it_was_seen(robot, monkeypatch):
    om.remember("the orange bottle", 0.0, -2.0, None, EPOCH)
    monkeypatch.setattr(lo, "_capture", lambda b, settle_s=2.5: (b"jpeg", {"stamp": None, "pose": None}))
    monkeypatch.setattr(lo, "_vlm_locate", lambda f, d: None)
    out = locate_object.invoke({"description": "the orange bottle"})
    assert "can't see" in out and "I saw the orange bottle" in out and "right" in out


# ── approach: memory first, then the checker ────────────────────────────────

@pytest.fixture
def scripted(monkeypatch, fake_twist):
    """Camera and VLM replies in order; every turn recorded."""
    turns, vlm = [], []
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5: (b"jpeg", {"stamp": (1, 2), "pose": b.pose}))
    monkeypatch.setattr(ap, "_vlm_locate", lambda f, d: vlm.pop(0) if vlm else None)
    monkeypatch.setattr(mv, "turn_robot", lambda b, deg: (turns.append(round(deg)) or (True, "")))
    return turns, vlm


def test_remembered_and_still_there_turns_to_it_and_goes(robot, scripted):
    turns, vlm = scripted
    om.remember("the orange bottle", 1.0, 1.73, (0, 0, 0), EPOCH)     # 60 deg to the left
    vlm.append((400.0, 250.0, (380.0, 200.0, 420.0, 300.0)))
    robot.replies = [_grounded(1.02, 1.74)]
    out = approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert turns == [60], "faced where it was, from where the robot is now"
    assert "still where I saw it" in out and robot.navs
    (e,) = om.recall("bottle", EPOCH)
    assert e["sightings"] == 2 and e["x"] == 1.02


def test_straight_ahead_needs_no_turn(robot, scripted):
    turns, vlm = scripted
    om.remember("the orange bottle", 2.0, 0.2, None, EPOCH)           # ~6 deg
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(2.0, 0.2)]
    approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert turns == []


def test_removed_is_forgotten_said_and_searched_for(robot, scripted):
    """The checker: someone took the bottle. Say so, forget it, search the
    other three quarters (the faced view was just checked)."""
    turns, vlm = scripted
    om.remember("the orange bottle", -1.0, 0.0, None, EPOCH)          # behind
    out = approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert [abs(turns[0])] + turns[1:] == [180, 90, 90, 90]   # dead behind: either way round
    assert "isn't where it was" in out and "full circle" in out
    assert om.recall("bottle", EPOCH) == [] and not robot.navs


def test_removed_but_found_elsewhere_in_the_search(robot, scripted):
    turns, vlm = scripted
    om.remember("the orange bottle", 0.0, 1.5, None, EPOCH)           # +90
    vlm.extend([None, (448.0, 250.0, None)])                          # not there; found one turn later
    robot.replies = [_grounded(-1.5, 0.0)]
    out = approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert turns == [90, 90] and "isn't where it was" in out and robot.navs
    (e,) = om.recall("bottle", EPOCH)
    assert (e["x"], e["y"]) == (-1.5, 0.0)


def test_found_but_moved_far_replaces_the_old_spot(robot, scripted):
    turns, vlm = scripted
    om.remember("the orange bottle", 2.0, 0.0, None, EPOCH)
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(2.0, 1.0)]                             # 1 m from where it was
    out = approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert "moved about 1.0 m" in out
    (e,) = om.recall("bottle", EPOCH)
    assert (e["x"], e["y"]) == (2.0, 1.0)


def test_a_different_colour_is_not_recalled(robot, scripted):
    turns, vlm = scripted
    om.remember("the orange bottle", 0.0, 1.5, None, EPOCH)
    approach_described_object.invoke({"description": "the red bottle", "state": dict(STATE)})
    assert turns == [90, 90, 90], "a plain search: the orange one is not the red one"


def test_nothing_remembered_is_the_plain_search(robot, scripted):
    turns, vlm = scripted
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.5, 0.0)]
    approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert turns == [] and len(om.recall("bottle", EPOCH)) == 1


# ── a depth stall is retried once on a fresh photo ──────────────────────────

def test_a_depth_stall_is_retried_on_a_fresh_photo(robot, scripted, monkeypatch):
    """2026-09-26: the camera sent no depth for up to 9 s; the VLM had found
    the bottle and the tool gave up. Now: wait, new photo, VLM, ground."""
    monkeypatch.setattr(ap, "_STALL_RETRY_S", 0.0)
    turns, vlm = scripted
    vlm.extend([(448.0, 250.0, None), (450.0, 250.0, None)])
    robot.replies = [{"ok": False, "reason": "no_depth_near_stamp"},
                     {"ok": False, "reason": "no_depth_frame"},       # the still-robot re-ground
                     _grounded(1.5, 0.0)]
    out = approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert robot.navs and "on my way" in out.lower()


def test_other_depth_failures_are_not_retried(robot, scripted, monkeypatch):
    monkeypatch.setattr(ap, "_STALL_RETRY_S", 0.0)
    turns, vlm = scripted
    vlm.append((448.0, 250.0, None))
    robot.replies = [{"ok": False, "reason": "no_depth_at_pixel"}]
    out = approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert "no_depth_at_pixel" in out and len(robot.queries) == 1
