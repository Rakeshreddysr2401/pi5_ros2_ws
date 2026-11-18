"""tools/approach.py — standoff geometry and the VLM approach path.

Runs off-robot against a StubBridge: no camera, no Jetson, no LLM.
"""

import math

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge, NAVIGATE_TOOLS
from langrobo_core.tools.approach import (approach_described_object,
                                          compute_standoff_goal,
                                          list_saved_locations)
import langrobo_core.tools.approach as ap

_bridge._instance = None
_bridge.init(StubBridge())

VOICE_STATE = {"channel": "voice", "sender_name": "voice", "messages": []}


# ── Registration ────────────────────────────────────────────────────────────

def test_navigate_has_the_approach_tools():
    names = {t.name for t in NAVIGATE_TOOLS}
    for tool_name in ("approach_described_object", "scan_surroundings",
                      "list_saved_locations"):
        assert tool_name in names


# ── Standoff geometry (pure) ────────────────────────────────────────────────

def test_standoff_goal_straight_line():
    """Object 2 m ahead, standoff 0.45 → park at 1.55 m, still facing it."""
    gx, gy, yaw = compute_standoff_goal(0.0, 0.0, 2.0, 0.0, 0.45)
    assert (gx, gy) == pytest.approx((1.55, 0.0))
    assert yaw == pytest.approx(0.0)


def test_standoff_goal_diagonal_faces_object():
    gx, gy, yaw = compute_standoff_goal(0.0, 0.0, 3.0, 3.0, 0.45)
    assert yaw == pytest.approx(45.0)
    # The goal stays on the robot→object line, standoff short of the object.
    assert math.hypot(3.0 - gx, 3.0 - gy) == pytest.approx(0.45)


def test_standoff_goal_already_close_turns_in_place():
    """Inside the standoff ring: don't back away, just face it."""
    gx, gy, yaw = compute_standoff_goal(1.0, 1.0, 1.2, 1.0, 0.45)
    assert (gx, gy) == pytest.approx((1.0, 1.0))
    assert yaw == pytest.approx(0.0)


def test_standoff_goal_on_top_of_the_object_does_not_divide_by_zero():
    gx, gy, _ = compute_standoff_goal(2.0, 2.0, 2.0, 2.0, 0.45)
    assert (gx, gy) == pytest.approx((2.0, 2.0))


# ── The VLM approach path ───────────────────────────────────────────────────

def test_approach_starts_nav_when_the_vlm_finds_it(monkeypatch):
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", None))
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: (100.0, 50.0))
    goals = []
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0, stamp=None, box=None, what=None: {
                            "ok": True, "depth_m": 1.8,
                            "goal": {"x": 1.2, "y": 0.3, "yaw": 0.0}})
    monkeypatch.setattr(_bridge.get(), "start_nav_to_pose",
                        lambda *a, **k: goals.append(a))
    out = approach_described_object.invoke(
        {"description": "red bottle", "state": dict(VOICE_STATE)})
    assert "1.8" in out and "on my way" in out.lower()
    assert goals, "a Nav2 goal should have been sent"


def test_approach_is_honest_when_the_jetson_is_silent(monkeypatch):
    """A timeout means the query never arrived — NOT that grounding failed."""
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", None))
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: (10.0, 10.0))
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0, stamp=None, box=None, what=None: {
                            "ok": False, "reason": "no_reply_from_jetson"})
    out = approach_described_object.invoke(
        {"description": "mug", "state": dict(VOICE_STATE)})
    assert "isn't answering" in out


def test_approach_reports_a_depth_failure_with_its_reason(monkeypatch):
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", None))
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: (10.0, 10.0))
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0, stamp=None, box=None, what=None: {
                            "ok": False, "reason": "no_depth_at_pixel"})
    out = approach_described_object.invoke(
        {"description": "mug", "state": dict(VOICE_STATE)})
    assert "glass, a mirror" in out and "have not driven" in out


def test_approach_says_it_lost_sight_not_reflective(monkeypatch):
    """2026-10-04: the photo's depth expired, the fresh photo no longer showed
    the person (she walked off) -- the reply blamed reflectivity."""
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", None))
    found = iter([(10.0, 10.0)])
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: next(found, None))
    monkeypatch.setattr(ap, "_STALL_RETRY_S", 0.0)
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0, stamp=None, box=None, what=None: {
                            "ok": False, "reason": "snapshot_expired"})
    out = approach_described_object.invoke(
        {"description": "the person in the floral dress", "state": dict(VOICE_STATE)})
    assert "no longer in view" in out and "reflective" not in out


@pytest.mark.parametrize("description", [
    "the man who is my Dad", "my dad", "Dad", "my elder brother", "amma", "nanna"])
def test_approach_asks_what_a_named_person_looks_like(monkeypatch, description):
    """2026-10-04: "go to my dad" drove at the first man in view -- the brother."""
    monkeypatch.setattr(ap, "_capture", lambda *a, **k: pytest.fail("must not look"))
    out = approach_described_object.invoke(
        {"description": description, "state": dict(VOICE_STATE)})
    assert "can't recognise who people are" in out and "have not moved" in out


@pytest.mark.parametrize("description", [
    "my dad in the blue shirt", "the man by the door", "my black backpack",
    "the person in the floral dress", "the red bottle"])
def test_a_described_person_or_thing_passes_the_guard(description):
    assert not ap._names_a_person_only(description)


def test_approach_gives_up_honestly_after_a_full_circle(monkeypatch):
    """The VLM never finds it. The robot must say so, not invent a goal."""
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", None))
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: None)
    out = approach_described_object.invoke(
        {"description": "elephant", "state": dict(VOICE_STATE)})
    assert "couldn't spot" in out


def test_approach_admits_a_dead_camera(monkeypatch):
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (None, None))
    out = approach_described_object.invoke(
        {"description": "mug", "state": dict(VOICE_STATE)})
    assert "fresh image" in out


# ── Saved locations ─────────────────────────────────────────────────────────

def test_list_saved_locations():
    out = list_saved_locations.invoke({})
    assert "kitchen" in out or "No locations saved" in out


# ── Park where the camera still sees it (2026-11-18) ──────────────────────────

def test_view_standoff_backs_off_for_things_above_or_below_the_lens():
    assert ap.view_standoff(None, 0.175) == ap._STANDOFF_M
    assert ap.view_standoff(0.18, 0.175) == ap._STANDOFF_M          # level with the lens
    assert ap.view_standoff(0.45, 0.175) > 0.8                       # on a shelf
    assert ap.view_standoff(5.0, 0.175) == ap._MAX_VIEW_STANDOFF_M   # capped


def _drive_to(monkeypatch, object_z):
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", None))
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: (100.0, 50.0))
    monkeypatch.setattr(_bridge.get(), "get_current_pose", lambda: (0.0, 0.0, 0.0))
    goals = []
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0, stamp=None, box=None, what=None: {
                            "ok": True, "depth_m": 2.0,
                            "object": {"x": 2.0, "y": 0.0, "z": object_z},
                            "camera": {"t": [0.16, 0.0, 0.175]},
                            "goal": {"x": 1.55, "y": 0.0, "yaw": 0.0}})
    monkeypatch.setattr(_bridge.get(), "start_nav_to_pose", lambda *a, **k: goals.append(a))
    approach_described_object.invoke({"description": "blue bottle", "state": dict(VOICE_STATE)})
    assert goals
    return goals[-1]


def test_approach_parks_farther_back_from_a_thing_up_on_a_shelf(monkeypatch):
    x, y, yaw = _drive_to(monkeypatch, object_z=0.5)[:3]
    assert x == pytest.approx(2.0 - ap.view_standoff(0.5, 0.175), abs=0.01)
    assert x < 1.3 and y == pytest.approx(0.0) and yaw == pytest.approx(0.0)


def test_approach_keeps_the_jetson_goal_for_a_thing_at_lens_height(monkeypatch):
    x, y, _ = _drive_to(monkeypatch, object_z=0.17)[:3]
    assert (x, y) == pytest.approx((1.55, 0.0))


# ── A closer look before the last metre (2026-11-18) ──────────────────────────

def _far_find(monkeypatch, second_box, leg=None):
    shots = iter([(100.0, 50.0, None), second_box])
    monkeypatch.setattr(ap, "_capture", lambda b, settle_s=2.5, source="search": (b"jpeg", None))
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: next(shots))
    monkeypatch.setattr(_bridge.get(), "get_current_pose", lambda: (0.0, 0.0, 0.0))
    legs, goals = [], []
    monkeypatch.setattr(_bridge.get(), "reach_and_wait",
                        lambda x, y, yaw, timeout=240.0: legs.append((x, y)) or (leg or {"ok": True}))
    measured = iter([
        {"ok": True, "depth_m": 2.5, "object": {"x": 2.5, "y": 0.0, "z": 0.18},
         "goal": {"x": 2.05, "y": 0.0, "yaw": 0.0}},            # first, far: the wall
        {"ok": True, "depth_m": 1.0, "object": {"x": 2.0, "y": 0.3, "z": 0.18},
         "goal": {"x": 1.6, "y": 0.25, "yaw": 0.0}},            # closer: the thing itself
    ])
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0, stamp=None, box=None, what=None: next(measured))
    monkeypatch.setattr(_bridge.get(), "start_nav_to_pose", lambda *a, **k: goals.append(a))
    out = approach_described_object.invoke({"description": "helmet", "state": dict(VOICE_STATE)})
    return out, legs, goals


def test_a_far_find_is_looked_at_again_from_closer_and_driven_on_the_new_measurement(monkeypatch):
    out, legs, goals = _far_find(monkeypatch, (120.0, 60.0, None))
    assert legs == [(1.5, 0.0)]                        # 1.0 m short of the first measurement
    assert goals and goals[-1][:2] == (1.6, 0.25)      # the closer measurement's goal


def test_not_found_from_closer_keeps_the_first_measurement(monkeypatch):
    out, legs, goals = _far_find(monkeypatch, None)
    assert legs and goals[-1][:2] == (2.05, 0.0)


def test_a_close_find_needs_no_closer_look(monkeypatch):
    monkeypatch.setattr(ap, "CLOSER_LOOK_BEYOND_M", 5.0)
    out, legs, goals = _far_find(monkeypatch, (120.0, 60.0, None))
    assert legs == [] and goals[-1][:2] == (2.05, 0.0)
