"""remember_object: "remember the blue bottle" saves where the THING is, measured, pinned
-- and "go to the blue bottle" then goes there (owner, 2026-11-16)."""
import pytest

from langrobo_core.services import object_memory as om
from langrobo_core.tools import memory_lookup as ml
from langrobo_core.tools.approach import approach_described_object
from langrobo_core.tools.remember_object import remember_object

from test_approach_photos import photo_answer  # noqa: F401  (fixture)
from test_object_memory import EPOCH, STATE, _grounded


@pytest.fixture(autouse=True)
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_OBJECT_MEMORY", str(tmp_path / "om.json"))
    om.set_home(None)
    monkeypatch.setattr(ml.embedder, "embed", lambda texts: None)
    yield


def _remember(desc="the blue bottle"):
    return remember_object.invoke({"description": desc, "state": dict(STATE)})


def test_saves_where_the_thing_is_pinned(robot, scripted):
    turns, vlm = scripted
    vlm.append((300.0, 260.0, (280.0, 220.0, 320.0, 300.0)))
    robot.replies = [_grounded(1.4, -0.3, depth=1.4)]
    out = _remember()
    assert "remember the blue bottle" in out and "1.4 m" in out
    hits = om.recall("blue bottle", EPOCH)
    assert len(hits) == 1 and (hits[0]["x"], hits[0]["y"]) == (1.4, -0.3) and hits[0]["pinned"]
    assert turns == [] and not robot.navs, "it does not move the robot"


def test_a_pinned_spot_never_ages_out():
    om.remember("blue bottle", 1.0, 0.0, None, EPOCH, when=0.0, pinned=True)
    om.remember("red cup", 2.0, 0.0, None, EPOCH, when=0.0)
    names = [e["description"] for e in om.recall("", EPOCH, now=60 * 86400.0)]
    assert names == ["blue bottle"]


def test_then_go_to_it_uses_the_remembered_spot(robot, scripted, photo_answer):
    turns, vlm = scripted
    vlm.append((300.0, 260.0, None))
    robot.replies = [_grounded(4.0, 0.0, depth=4.0)]
    _remember()
    robot.legs = [{"ok": True, "result": "reached"}]
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(4.01, 0.0)]
    out = approach_described_object.invoke({"description": "the blue bottle", "state": dict(STATE)})
    assert robot.leg_goals == [(3.0, 0.0, 0.0)] and "still where I saw it" in out and robot.navs


def test_not_in_view_says_so(robot, scripted):
    out = _remember()
    assert "don't see the blue bottle" in out and om.recall("", EPOCH) == []


def test_seen_but_not_measured_says_why(robot, scripted):
    turns, vlm = scripted
    vlm.append((300.0, 260.0, None))
    robot.replies = [{"ok": False, "reason": "no_depth_at_pixel"}]
    out = _remember()
    assert "couldn't measure" in out and "glass" in out and om.recall("", EPOCH) == []
