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
    monkeypatch.setattr(ap, "_fresh_frame", lambda b, settle_s=2.5: b"jpeg")
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: (100.0, 50.0))
    goals = []
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0: {
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
    monkeypatch.setattr(ap, "_fresh_frame", lambda b, settle_s=2.5: b"jpeg")
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: (10.0, 10.0))
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0: {
                            "ok": False, "reason": "no_reply_from_jetson"})
    out = approach_described_object.invoke(
        {"description": "mug", "state": dict(VOICE_STATE)})
    assert "isn't answering" in out


def test_approach_reports_a_depth_failure_with_its_reason(monkeypatch):
    monkeypatch.setattr(ap, "_fresh_frame", lambda b, settle_s=2.5: b"jpeg")
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: (10.0, 10.0))
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0: {
                            "ok": False, "reason": "no_depth_at_pixel"})
    out = approach_described_object.invoke(
        {"description": "mug", "state": dict(VOICE_STATE)})
    assert "no_depth_at_pixel" in out


def test_approach_gives_up_honestly_after_a_full_circle(monkeypatch):
    """The VLM never finds it. The robot must say so, not invent a goal."""
    monkeypatch.setattr(ap, "_fresh_frame", lambda b, settle_s=2.5: b"jpeg")
    monkeypatch.setattr(ap, "_vlm_locate", lambda frame, desc: None)
    out = approach_described_object.invoke(
        {"description": "elephant", "state": dict(VOICE_STATE)})
    assert "couldn't spot" in out


def test_approach_admits_a_dead_camera(monkeypatch):
    monkeypatch.setattr(ap, "_fresh_frame", lambda b, settle_s=2.5: None)
    out = approach_described_object.invoke(
        {"description": "mug", "state": dict(VOICE_STATE)})
    assert "fresh image" in out


# ── Saved locations ─────────────────────────────────────────────────────────

def test_list_saved_locations():
    out = list_saved_locations.invoke({})
    assert "kitchen" in out or "No locations saved" in out
