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

    def ground_pixel(self, u, v, timeout=4.0, stamp=None, box=None):
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
    monkeypatch.setattr(lo, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", {"stamp": None, "pose": (0, 0, 0)}))
    monkeypatch.setattr(lo, "_vlm_locate", lambda f, d: (100.0, 50.0, None))
    robot.replies = [_grounded(1.2, 0.3)]
    locate_object.invoke({"description": "the orange bottle"})
    (e,) = om.recall("bottle", EPOCH)
    assert (e["x"], e["y"]) == (1.2, 0.3) and e["seen_from"] == [0, 0, 0]


def test_locate_not_in_view_says_where_it_was_seen(robot, monkeypatch):
    om.remember("the orange bottle", 0.0, -2.0, None, EPOCH)
    monkeypatch.setattr(lo, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", {"stamp": None, "pose": None}))
    monkeypatch.setattr(lo, "_vlm_locate", lambda f, d: None)
    out = locate_object.invoke({"description": "the orange bottle"})
    assert "can't see" in out and "I saw the orange bottle" in out and "right" in out


# ── approach: memory first, then the checker ────────────────────────────────

@pytest.fixture
def scripted(monkeypatch, fake_twist):
    """Camera and VLM replies in order; every turn recorded."""
    turns, vlm = [], []
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", {"stamp": (1, 2), "pose": b.pose}))
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


def test_removed_is_forgotten_after_searching_its_spot(robot, scripted):
    """The checker, close by: someone took the bottle. Already at the
    viewpoint, so no drive: search that spot in 45 degree steps (the faced
    view was just checked), then say so and forget it."""
    turns, vlm = scripted
    om.remember("the orange bottle", -1.0, 0.0, None, EPOCH)          # behind, 1 m
    out = approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert [abs(turns[0])] + turns[1:] == [180] + [45] * 7   # dead behind: either way round
    assert "looked all around that spot" in out and "isn't there any more" in out
    assert om.recall("bottle", EPOCH) == [] and not robot.navs


def test_cannot_reach_its_spot_searches_here_and_does_not_forget(robot, scripted):
    """Not visible from 1.5 m and reach is down: search from here, and keep
    the memory -- nobody has looked at that spot up close."""
    turns, vlm = scripted
    om.remember("the orange bottle", 0.0, 1.5, None, EPOCH)           # +90, 1.5 m
    vlm.extend([None, (448.0, 250.0, None)])                          # not seen; then found ahead
    robot.replies = [_grounded(-1.5, 0.0)]
    out = approach_described_object.invoke({"description": "the orange bottle", "state": dict(STATE)})
    assert turns == [90] and "couldn't get to that spot" in out and robot.navs
    assert len(om.recall("bottle", EPOCH)) == 2, "the old spot is not forgotten"


# ── far away: go to where it was, then look (owner, 2026-09-27) ─────────────

def test_far_away_drives_to_its_spot_then_looks_and_goes(robot, scripted):
    turns, vlm = scripted
    om.remember("the white chair", 4.0, 0.0, None, EPOCH)             # 4 m ahead
    robot.legs = [{"ok": True, "result": "reached"}]
    vlm.append((448.0, 250.0, None))                                  # there, from the viewpoint
    robot.replies = [_grounded(4.02, 0.01)]
    out = approach_described_object.invoke({"description": "white chair", "state": dict(STATE)})
    assert robot.leg_goals == [(3.0, 0.0, 0.0)], "1 m in front of where it was, facing it"
    assert turns == [] and "still where I saw it" in out and robot.navs


def test_far_away_and_gone_searches_that_spot_then_forgets(robot, scripted):
    turns, vlm = scripted
    om.remember("the white chair", 4.0, 0.0, None, EPOCH)
    robot.legs = [{"ok": True, "result": "reached"}]
    out = approach_described_object.invoke({"description": "white chair", "state": dict(STATE)})
    assert robot.pose[:2] == (3.0, 0.0) and turns == [45] * 7, "the circle is around THAT spot"
    assert "looked all around that spot" in out
    assert om.recall("chair", EPOCH) == []


def test_found_near_its_old_spot_is_the_same_entry_updated(robot, scripted):
    turns, vlm = scripted
    om.remember("the white chair", 4.0, 0.0, None, EPOCH)
    robot.legs = [{"ok": True, "result": "reached"}]
    vlm.extend([None, None, (448.0, 250.0, None)])                    # found two turns into the spot search
    robot.replies = [_grounded(4.1, 0.9)]
    out = approach_described_object.invoke({"description": "white chair", "state": dict(STATE)})
    assert turns == [45, 45] and "moved about 0.9 m" in out
    (e,) = om.recall("chair", EPOCH)
    assert (e["x"], e["y"]) == (4.1, 0.9)


def test_a_new_command_stops_the_drive_to_its_spot(robot, scripted):
    turns, vlm = scripted
    om.remember("the white chair", 4.0, 0.0, None, EPOCH)
    robot.legs = [{"ok": False, "result": "interrupted"}]
    out = approach_described_object.invoke({"description": "white chair", "state": dict(STATE)})
    assert out.startswith("Stopped") and not robot.navs and turns == []
    assert len(om.recall("chair", EPOCH)) == 1


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
    assert turns == [45] * 7, "a plain search: the orange one is not the red one"


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


def test_two_matches_far_apart_says_there_is_another(robot, scripted):
    """Floor test 2026-09-27: two white boxes; "the one you saw before" went to
    the other one and nothing told the user there was a choice."""
    turns, vlm = scripted
    om.remember("white box", -1.35, -0.28, None, EPOCH, when=time.time() - 200)
    om.remember("white box", 1.15, -0.12, None, EPOCH)                # the newer one, ahead
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.15, -0.12)]
    out = approach_described_object.invoke({"description": "white rectangular box", "state": dict(STATE)})
    assert "also remember another white rectangular box about 1.4 m away" in out


# ── "go near it" means the thing IN THE PHOTO we talked about ───────────────

def test_go_near_it_grounds_in_the_conversation_photo_not_by_name(robot, scripted):
    """Floor test 2026-09-27, replayed: "what do you see?" -> a white box,
    photographed facing -169 deg from (-0.6, -0.2). Teleoped away to
    (0.54, -0.09) facing +15. A DIFFERENT white box sits in memory right in
    front. "Go near it" must go to the one in the photo, 2 m behind."""
    import base64
    from langchain_core.messages import HumanMessage
    from langrobo_core.tools import photos
    turns, vlm = scripted
    jpeg = b"\xff\xd8the-look-photo"
    photos.record(jpeg, (100, 5), (-0.6, -0.2, -169.0), time.time() - 90, EPOCH, "look")
    om.remember("white box", 1.15, -0.12, None, EPOCH)               # the wrong one, ahead
    robot.pose = (0.54, -0.09, 15.0)
    robot.legs = [{"ok": True, "result": "reached"}]
    state = dict(STATE, messages=[
        HumanMessage(content=[{"type": "text", "text": "[Camera view ...]"},
                              {"type": "image_url", "image_url": {
                                  "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]),
        HumanMessage(content="go near it")])
    vlm.extend([(300.0, 250.0, (250.0, 200.0, 350.0, 300.0)),        # in the photo
                (448.0, 250.0, None)])                               # seen again from the viewpoint
    robot.replies = [_grounded(-1.35, -0.28), _grounded(-1.34, -0.27)]
    out = approach_described_object.invoke(
        {"description": "the white rectangular box on the marble floor", "state": state})
    # 1.9 m away: close enough to judge from here -> turn round to it and look
    assert len(turns) == 1 and abs(turns[0]) == pytest.approx(171, abs=2), \
        "turned to the box IN THE PHOTO, behind"
    assert robot.navs and "also remember" not in out and "still where I saw it" in out
    nav_x, nav_y = robot.navs[0][:2]
    assert nav_x < 0, "the final drive is to the box behind, not the one ahead"


# ── a refused turn does not end the search (floor test 2026-09-27) ──────────

def _turns_refused_after(n_ok, refuse_left_only=True):
    """turn_robot that allows n_ok left turns, then refuses left (or both)."""
    done = []

    def turn(bridge, deg):
        if deg > 0 and sum(1 for d in done if d > 0) >= n_ok:
            return False, "refused: something 0.33 m away is in the +45 deg swing"
        if deg < 0 and not refuse_left_only:
            return False, "refused: something 0.30 m away is in the -45 deg swing"
        done.append(round(deg))
        return True, ""
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


def test_blocked_both_ways_says_so(robot, scripted, monkeypatch):
    turns, vlm = scripted
    turn, done = _turns_refused_after(1, refuse_left_only=False)
    monkeypatch.setattr(mv, "turn_robot", turn)
    out = approach_described_object.invoke({"description": "blue and white robot", "state": dict(STATE)})
    assert "couldn't turn either way" in out and "2 view(s)" in out and not robot.navs


def test_photo_without_depth_uses_the_newest_if_the_robot_has_not_moved(robot, scripted):
    """The photo landed in a depth gap (no_depth_near_stamp); the robot is
    where it took it, so the newest depth is the same view."""
    import base64
    from langchain_core.messages import HumanMessage
    from langrobo_core.tools import photos
    turns, vlm = scripted
    jpeg = b"\xff\xd8photo-in-a-depth-gap"
    photos.record(jpeg, (200, 7), (0.0, 0.0, 0.0), time.time() - 60, EPOCH, "look")
    state = dict(STATE, messages=[HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}])])
    vlm.extend([(300.0, 250.0, None), (448.0, 250.0, None)])
    robot.replies = [{"ok": False, "reason": "no_depth_near_stamp"},  # the photo's own depth
                     _grounded(1.2, 0.1),                             # newest depth, robot still
                     _grounded(1.2, 0.1)]                             # the final look
    out = approach_described_object.invoke({"description": "blue and white robot", "state": state})
    assert robot.navs and "still where I saw it" in out


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
