"""approach_described_object with LONG-TERM object memory (owner, 2026-11-16).

Not in this session's photos -> long-term memory, matched by meaning (a TV is the
"black television" it surveyed yesterday) -> the same steps as a photo answer: drive
to a viewpoint, LOOK, then go; gone -> forget that sighting and search that spot.
Off-robot: the fake robot, scripted camera/VLM of test_object_memory, a scripted
language model for the by-meaning choice.
"""
import pytest

from langrobo_core.services import object_memory as om
from langrobo_core.tools import memory_lookup as ml
from langrobo_core.tools.approach import approach_described_object

from test_approach_photos import photo_answer  # noqa: F401  (fixture)
from test_object_memory import EPOCH, STATE, _grounded


@pytest.fixture(autouse=True)
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_OBJECT_MEMORY", str(tmp_path / "om.json"))
    om.set_home(None)
    monkeypatch.setattr(ml.embedder, "embed", lambda texts: None)     # no sentence model: the judge sees all
    yield


def _go(desc):
    return approach_described_object.invoke({"description": desc, "state": dict(STATE)})


def test_remembered_by_meaning_drives_there_looks_and_goes(robot, scripted, photo_answer, monkeypatch):
    turns, vlm = scripted
    om.remember("black television", 4.0, 0.0, None, EPOCH)
    monkeypatch.setattr(ml, "_ask_llm", lambda p: "1")
    robot.legs = [{"ok": True, "result": "reached"}]
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(4.02, 0.01)]
    out = _go("the tv")
    assert robot.leg_goals == [(3.0, 0.0, 0.0)], "1 m in front of where memory put it"
    assert "still where I saw it" in out and robot.navs


def test_gone_from_its_remembered_spot_is_forgotten_and_searched_there(robot, scripted, photo_answer, monkeypatch):
    turns, vlm = scripted
    om.remember("black television", 4.0, 0.0, None, EPOCH)
    monkeypatch.setattr(ml, "_ask_llm", lambda p: "1")
    robot.legs = [{"ok": True, "result": "reached"}]
    out = _go("the tv")
    assert robot.pose[:2] == (3.0, 0.0) and turns == [45] * 7, "the circle is around THAT spot"
    assert om.recall("", EPOCH) == [], "a sighting the look did not confirm is forgotten"
    assert "isn't there now" in out


def test_in_this_sessions_photos_needs_no_long_term_memory(robot, scripted, photo_answer, monkeypatch):
    from test_approach_photos import _seen_at
    turns, vlm = scripted
    om.remember("black television", 4.0, 0.0, None, EPOCH)
    monkeypatch.setattr(ml, "find", lambda *a, **k: pytest.fail("photos answered: memory not asked"))
    photo_answer["answer"] = _seen_at(2.0, 0.2)
    vlm.append((448.0, 250.0, None))
    robot.replies = [_grounded(2.0, 0.2)]
    _go("the tv")
    assert robot.navs


def test_nothing_remembered_searches_as_before(robot, scripted, photo_answer, monkeypatch):
    turns, vlm = scripted
    monkeypatch.setattr(ml, "_ask_llm", lambda p: pytest.fail("nothing remembered: no model call"))
    _go("the tv")
    assert turns == [45] * 7
