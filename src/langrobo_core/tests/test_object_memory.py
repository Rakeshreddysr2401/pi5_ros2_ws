"""Object memory and the checker (rover repo INTELLIGENCE_PLAN.md B3).

The owner's case: the robot saw a bottle from position 1; at position 2,
"go near the bottle" should turn to face where it was, check it is still
there ("sometimes the bottle may be removed by someone"), and go -- or say it
has gone and search.

Off-robot: StubBridge plus scripted camera / VLM / Jetson replies.
"""

import math

import time

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.services import object_memory as om
from langrobo_core.tools import _bridge
from langrobo_core.tools import approach as ap
from langrobo_core.tools import locate as lo
from langrobo_core.tools import movement as mv
from langrobo_core.tools.approach import approach_described_object
from langrobo_core.tools.locate import locate_object

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
    ("white box", "white chair", False),                   # colour alone is not the thing
    ("white chair", "chair", True),
    ("the white one", "white chair", True),                # only a colour asked: colour decides
    # floor test 2026-09-27: the model's long description vs the survey's short label
    ("the white rectangular box on the marble floor", "white box", True),
    ("white rectangular box you seen before", "white box", True),
    ("white rectangular box", "white rectangular table", False),  # same adjective, different thing
    ("white box on the floor", "floor lamp", False),        # "floor" is where, not what
    ("coffee mug", "mug", True),
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

    def ground_pixel(self, u, v, timeout=4.0, stamp=None, box=None, what=None):
        self.queries.append((u, v))
        return self.replies.pop(0)

    def start_nav_to_pose(self, x, y, yaw_deg, label=""):
        self.navs.append((x, y, yaw_deg, label))

    legs = None          # None = reach not running; else a list of results
    def reach_and_wait(self, x, y, yaw_deg, timeout=240.0):
        if self.legs is None:
            return super().reach_and_wait(x, y, yaw_deg, timeout)
        self.leg_goals = getattr(self, "leg_goals", []) + [(x, y, yaw_deg)]
        res = self.legs.pop(0)
        if res.get("ok"):
            self.pose = (x, y, yaw_deg)          # drove there
        return res


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





# ── locate_object writes memory ─────────────────────────────────────────────



# ── approach: memory first, then the checker ────────────────────────────────

@pytest.fixture
def scripted(monkeypatch, fake_twist):
    """Camera and VLM replies in order; every turn recorded."""
    turns, vlm = [], []
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", {"stamp": (1, 2), "pose": b.pose}))
    monkeypatch.setattr(ap, "_vlm_locate", lambda f, d: vlm.pop(0) if vlm else None)
    monkeypatch.setattr(mv, "turn_robot", lambda b, deg: (turns.append(round(deg)) or _rotate(b, deg)))
    return turns, vlm


def _rotate(bridge, deg):
    """The pose turns with the robot, as fusion's does: the search aims each
    view from the measured heading."""
    if bridge.pose:
        x, y, h = bridge.pose
        bridge.pose = (x, y, (h + deg + 180.0) % 360.0 - 180.0)
    return True, ""






# ── far away: go to where it was, then look (owner, 2026-09-27) ─────────────








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



# ── "go near it" means the thing IN THE PHOTO we talked about ───────────────


# ── a refused turn does not end the search (floor test 2026-09-27) ──────────

def _turns_refused_after(n_ok, refuse_left_only=True, swung=0.0):
    """turn_robot that allows n_ok left turns, then refuses left (or both).
    swung: how far a refused left turn got before goal_exec stopped it."""
    done = []

    def turn(bridge, deg):
        if deg > 0 and sum(1 for d in done if d > 0) >= n_ok:
            _rotate(bridge, swung)
            return False, "refused: something 0.33 m away is in the +45 deg swing"
        if deg < 0 and not refuse_left_only:
            return False, "refused: something 0.30 m away is in the -45 deg swing"
        done.append(round(deg))
        return _rotate(bridge, deg)
    return turn, done


def test_left_blocked_finishes_the_circle_from_the_right(robot, scripted, monkeypatch):
    turns, vlm = scripted
    turn, done = _turns_refused_after(1)
    monkeypatch.setattr(mv, "turn_robot", turn)
    vlm.extend([None, None, None, (448.0, 250.0, None)])             # found at the 4th view
    robot.replies = [_grounded(1.0, -1.0)]
    out = approach_described_object.invoke({"description": "blue and white robot", "state": dict(STATE)})
    # views: 0, +45 (left), then -45 (a 90 deg turn back past 0), then -90
    assert done == [45, -90, -45] and robot.navs and "couldn't turn" not in out


def test_a_refused_turn_that_swung_part_way_is_turned_back_from(robot, scripted, monkeypatch):
    """goal_exec stopped the refused +45 after 20 deg: the next view (-45 from
    the start) is 65 deg back from where the robot really is, not 90."""
    turns, vlm = scripted
    turn, done = _turns_refused_after(1, swung=20.0)
    monkeypatch.setattr(mv, "turn_robot", turn)
    vlm.extend([None, None, (448.0, 250.0, None)])                   # found at the 3rd view
    robot.replies = [_grounded(1.0, -1.0)]
    approach_described_object.invoke({"description": "blue and white robot", "state": dict(STATE)})
    assert done == [45, -110] and robot.pose[2] == pytest.approx(-45.0)


def test_blocked_both_ways_says_so(robot, scripted, monkeypatch):
    turns, vlm = scripted
    turn, done = _turns_refused_after(1, refuse_left_only=False)
    monkeypatch.setattr(mv, "turn_robot", turn)
    out = approach_described_object.invoke({"description": "blue and white robot", "state": dict(STATE)})
    assert "couldn't turn either way" in out and "2 view(s)" in out and not robot.navs



# ── "go near it" about a photo the survey has already done: no VLM call ─────

def _conversation_photo(jpeg, stamp):
    import base64
    from langchain_core.messages import HumanMessage
    from langrobo_core.tools import photos
    photos.record(jpeg, stamp, (0.0, 0.0, 0.0), time.time() - 30, EPOCH, "look")
    return dict(STATE, messages=[HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}])])




def test_objects_from_one_photo_have_their_own_ids():
    """The photo survey stores every object in a photo with that photo's time:
    ids must still differ, or forgetting one forgets them all."""
    a = om.remember("white chair", 1.0, 0.0, None, EPOCH, when=500.0)
    b = om.remember("black bag", 2.0, 1.0, None, EPOCH, when=500.0)
    assert a["id"] != b["id"]
    assert om.forget(a["id"], EPOCH)
    assert [e["description"] for e in om.recall("", EPOCH, now=510.0)] == ["black bag"]


def test_a_file_with_repeated_ids_is_repaired_on_load():
    """Memory written before ids were unique: one id per photo."""
    import json
    with open(om.path(), "w") as f:
        json.dump({"epoch": EPOCH, "objects": [
            {"id": "000000500", "description": "white chair", "x": 1.0, "y": 0.0, "seen_at": 500.0},
            {"id": "000000500", "description": "black bag", "x": 2.0, "y": 1.0, "seen_at": 500.0}]}, f)
    first = {e["description"]: e["id"] for e in om.recall("", EPOCH, now=510.0)}
    assert first["white chair"] != first["black bag"]
    again = {e["description"]: e["id"] for e in om.recall("", EPOCH, now=510.0)}
    assert again == first, "the same file gives the same ids every time"
    assert om.forget(first["black bag"], EPOCH)
    assert [e["description"] for e in om.recall("", EPOCH, now=510.0)] == ["white chair"]
