"""approach_described_object with the photo log as memory (2026-10-02).

The owner's case, unchanged: the robot saw a bottle from position 1; at
position 2, "go near the bottle" turns to where it was, checks it is still
there (someone may have moved it), and goes -- or searches. What changed is
WHERE "where it was" comes from: one question over every photo of the session
(photo_recall.ask), placed with that photo's depth and pose -- not a stored
list of objects.

Off-robot: the fake robot and scripted camera/VLM of test_object_memory, and
a scripted photo-log answer.
"""
import math

import pytest

from langrobo_core.tools import approach as ap
from langrobo_core.tools import photos as ph
from langrobo_core.tools.approach import approach_described_object, scan_surroundings

from test_movement import fake_twist  # noqa: F401 -- fixture
from test_object_memory import STATE, _grounded, robot, scripted  # noqa: F401 -- fixtures


def _seen_at(x, y, pose=(0.0, 0.0, 0.0), photo=3, age=120.0):
    """What photo_recall.ask returns for a thing placed at (x, y), measured
    from `pose` (the robot now)."""
    dx, dy = x - pose[0], y - pose[1]
    return {"ok": True, "answer": "It is there.", "object": "thing", "photo": photo,
            "count": 8, "oldest_s": 300.0, "age_s": age, "box": (1, 1, 2, 2),
            "placed": {"x": x, "y": y, "goal": None, "depth_m": 1.0},
            "distance_m": math.hypot(dx, dy),
            "bearing_deg": (math.degrees(math.atan2(dy, dx)) - pose[2] + 180) % 360 - 180,
            "bearing_only": False, "ground_reason": None}


@pytest.fixture
def photo_answer(monkeypatch):
    """Set the photo log's answer; records every question asked of it."""
    box = {"answer": {"ok": False, "why": "no_photos"}, "asked": []}

    def ask(question, only=None, place=True):
        box["asked"].append(question)
        return box["answer"]
    monkeypatch.setattr(ap._recall, "ask", ask)
    return box


def _go(desc="the orange bottle"):
    return approach_described_object.invoke({"description": desc, "state": dict(STATE)})


# ── seen in a photo, close by: face it, look, go ────────────────────────────

def test_seen_and_still_there_turns_to_it_and_goes(robot, scripted, photo_answer):
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(1.0, 1.73)                    # 60 deg to the left
    vlm.append((400.0, 250.0, (380.0, 200.0, 420.0, 300.0)))
    robot.replies = [_grounded(1.02, 1.74)]
    out = _go()
    assert photo_answer["asked"] == ["where is the orange bottle?"]
    assert turns == [60], "faced where it was, from where the robot is now"
    assert "still where I saw it" in out and robot.navs


def test_straight_ahead_needs_no_turn(robot, scripted, photo_answer):
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(2.0, 0.2)                     # ~6 deg
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(2.0, 0.2)]
    _go()
    assert turns == []


def test_moved_since_the_photo_says_how_far(robot, scripted, photo_answer):
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(2.0, 0.0)
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(2.0, 1.0)]                           # 1 m from where it was
    assert "moved about 1.0 m" in _go()


def test_gone_from_its_spot_searches_there_and_says_so(robot, scripted, photo_answer):
    """Someone took it. Already at the viewpoint, so no drive: search that
    spot in 45 degree steps (the faced view was just checked), then say where
    it was last seen."""
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(-1.0, 0.0, photo=5)           # behind, 1 m
    out = _go()
    assert [abs(turns[0])] + turns[1:] == [180] + [45] * 7
    assert "photo 5" in out and "isn't there now" in out and not robot.navs


# ── seen far away: go to where it was, then look ────────────────────────────

def test_far_away_drives_to_its_spot_then_looks_and_goes(robot, scripted, photo_answer):
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(4.0, 0.0)                     # 4 m ahead
    robot.legs = [{"ok": True, "result": "reached"}]
    vlm.append((448.0, 250.0, None))                                # there, from the viewpoint
    robot.replies = [_grounded(4.02, 0.01)]
    out = _go("white chair")
    assert robot.leg_goals == [(3.0, 0.0, 0.0)], "1 m in front of where it was, facing it"
    assert turns == [] and "still where I saw it" in out and robot.navs


def test_far_away_and_gone_searches_that_spot(robot, scripted, photo_answer):
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(4.0, 0.0)
    robot.legs = [{"ok": True, "result": "reached"}]
    out = _go("white chair")
    assert robot.pose[:2] == (3.0, 0.0) and turns == [45] * 7, "the circle is around THAT spot"
    assert "isn't there now" in out


def test_cannot_reach_its_spot_searches_here(robot, scripted, photo_answer):
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(0.0, 1.5)                     # +90, 1.5 m
    vlm.extend([None, (448.0, 250.0, None)])                        # not seen; then found ahead
    robot.legs = [{"ok": False, "result": "failed", "why": "no path"}]
    robot.replies = [_grounded(-1.5, 0.0)]
    out = _go()
    assert turns == [90] and "couldn't get to that spot" in out and robot.navs


def test_a_new_command_stops_the_drive_to_its_spot(robot, scripted, photo_answer):
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(4.0, 0.0)
    robot.legs = [{"ok": False, "result": "interrupted"}]
    out = _go("white chair")
    assert out.startswith("Stopped") and not robot.navs and turns == []


def test_direction_only_faces_that_way_and_looks(robot, scripted, photo_answer):
    """The photo's depth is gone but the robot has not moved off: turn the
    way the photo says, and look."""
    turns, vlm = scripted
    photo_answer["answer"] = dict(_seen_at(0.0, 1.0), placed=None, distance_m=None,
                                  bearing_deg=70.0, bearing_only=True)
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(0.1, 1.2)]
    _go()
    assert turns == [70] and robot.navs


# ── not in any photo: the plain search ──────────────────────────────────────

def test_in_no_photo_is_the_plain_search(robot, scripted, photo_answer):
    turns, vlm = scripted
    photo_answer["answer"] = {"ok": True, "answer": "No.", "object": None, "photo": None,
                              "count": 8, "oldest_s": 300.0}
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.5, 0.0)]
    _go()
    assert turns == [] and robot.navs


def test_a_vision_model_failure_still_searches(robot, scripted, monkeypatch):
    turns, vlm = scripted

    def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(ap._recall, "ask", boom)
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.5, 0.0)]
    _go()
    assert robot.navs


# ── every view is asked about through the photo log ─────────────────────────

def test_a_logged_photo_is_asked_through_the_log(monkeypatch):
    """A one-photo prompt on the vision-tool slot would throw the cached
    photos out; a logged photo goes through photo_recall.locate_in."""
    asked = []
    monkeypatch.setattr(ap._recall, "locate_in",
                        lambda jpeg, desc: asked.append(desc) or (10.0, 20.0, None))
    assert ap._vlm_locate(b"jpeg", "the chair") == (10.0, 20.0, None)
    assert asked == ["the chair"]


def test_look_around_keeps_a_photo_per_stop(robot, scripted, monkeypatch):
    turns, _ = scripted
    took = []
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search":
                        (took.append(source) or b"jpeg", {"stamp": (1, 2)}))
    out = scan_surroundings.invoke({"state": dict(STATE)})
    assert turns == [60] * 6 and took == ["scan"] * 6
    assert "6 photo(s)" in out


@pytest.fixture(autouse=True)
def _empty_photo_log():
    ph.clear()
    yield
    ph.clear()


# ── progress lines during long searches (2026-10-02) ────────────────────────

def _spoken(robot, monkeypatch):
    said = []
    monkeypatch.setattr(robot, "publish_speech", lambda text: said.append(text))
    return said


def test_a_voice_search_says_it_is_looking(robot, scripted, photo_answer, monkeypatch):
    said = _spoken(robot, monkeypatch)
    turns, vlm = scripted
    vlm.extend([None] * 5 + [(448.0, 250.0, None)])            # found on the 6th view
    robot.replies = [_grounded(1.5, 0.0)]
    _go("the toy car")
    assert said == ["I don't see the toy car from here, so I'm looking around.",
                    "Still looking for the toy car."]


def test_telegram_gets_no_spoken_progress(robot, scripted, photo_answer, monkeypatch):
    said = _spoken(robot, monkeypatch)
    approach_described_object.invoke({"description": "the toy car",
                                      "state": dict(STATE, channel="telegram")})
    assert said == []


def test_found_at_once_needs_no_progress(robot, scripted, photo_answer, monkeypatch):
    said = _spoken(robot, monkeypatch)
    turns, vlm = scripted
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(1.5, 0.0)]
    _go("the toy car")
    assert said == []


def test_driving_to_where_it_was_seen_says_so(robot, scripted, photo_answer, monkeypatch):
    said = _spoken(robot, monkeypatch)
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(4.0, 0.0, age=180)
    robot.legs = [{"ok": True, "result": "reached"}]
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(4.02, 0.01)]
    _go("white chair")
    assert said == ["I saw the white chair over there 3 min ago. Going to check."]


def test_a_spoken_note_is_not_repeated_in_the_answer(robot, scripted, photo_answer, monkeypatch):
    said = _spoken(robot, monkeypatch)
    turns, vlm = scripted
    photo_answer["answer"] = _seen_at(-1.0, 0.0, photo=5)             # behind, gone
    out = _go()
    assert said and "wasn't" in said[0]
    assert "wasn't" not in out
