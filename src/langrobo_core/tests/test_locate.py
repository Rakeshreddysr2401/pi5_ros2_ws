"""tools/locate.py — measuring a distance without driving to it.

Runs off-robot against a StubBridge: no camera, no Jetson, no LLM.

The property under test throughout is that a number only ever reaches the user
when the depth sensor actually produced one. Every failure path must say so
plainly rather than degrade into a guess — an invented distance is worse than
"I can't measure that", because the user cannot tell it apart from a real one.
"""

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge, LOCAL_AGENT_TOOLS
from langrobo_core.tools.locate import describe_bearing, locate_object
import langrobo_core.tools.locate as lo

_bridge._instance = None
_bridge.init(StubBridge())

VOICE_STATE = {"channel": "voice", "sender_name": "voice", "messages": []}

_OK_REPLY = {
    "ok": True,
    "depth_m": 1.19,
    "goal": {"x": 0.255, "y": -0.463, "yaw": -2.65},
    "object": {"x": -0.142, "y": -0.676},
    "relative": {"forward_m": 1.36, "left_m": 0.58, "bearing_deg": 23.0},
}


def _patch(monkeypatch, uv=(200.0, 252.0), reply=None):
    monkeypatch.setattr(lo, "_fresh_frame", lambda b, settle_s=2.5: b"jpeg")
    monkeypatch.setattr(lo, "_vlm_locate", lambda frame, desc: uv)
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0: reply or dict(_OK_REPLY))


# ── Registration ────────────────────────────────────────────────────────────

def test_local_agent_has_locate_object():
    """It belongs to the multimodal agent — the one that fields "what are you
    looking at", and the one that would otherwise invent the distance."""
    assert "locate_object" in {t.name for t in LOCAL_AGENT_TOOLS}


# ── Bearing phrasing (pure) ─────────────────────────────────────────────────

@pytest.mark.parametrize("deg, expect", [
    (0.0, "straight ahead"),
    (5.0, "straight ahead"),
    (-9.9, "straight ahead"),
    (23.0, "slightly to my left"),
    (-23.0, "slightly to my right"),
    (60.0, "to my left"),
    (-60.0, "to my right"),
    (120.0, "far to my left"),
    (-179.0, "far to my right"),
])
def test_describe_bearing(deg, expect):
    assert describe_bearing(deg) == expect


def test_bearing_sign_convention_matches_the_jetson():
    """+ is the robot's LEFT, matching pixel_to_goal's atan2(left, forward).
    Getting this backwards would send a user the wrong way round the room."""
    assert "left" in describe_bearing(30.0)
    assert "right" in describe_bearing(-30.0)


# ── The measured path ───────────────────────────────────────────────────────

def test_reports_a_measured_distance_and_direction(monkeypatch):
    _patch(monkeypatch)
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "1.48 m away" in out             # hypot(1.36, 0.58)
    assert "slightly to my left" in out
    assert "1.36 m in front of me" in out
    assert "0.58 m to my left" in out


def test_gives_the_numbers_as_coordinates(monkeypatch):
    """The literal ask: coordinates relative to the robot. +x forward,
    +y left, REP-103, origin base_link — signed, so the sign carries the
    side and nothing has to be inferred from prose."""
    _patch(monkeypatch)
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "x +1.36 m" in out
    assert "y +0.58 m" in out
    assert "+x forward, +y left" in out


def test_coordinate_signs_flip_for_a_right_hand_object(monkeypatch):
    _patch(monkeypatch, reply={
        "ok": True, "depth_m": 1.36,
        "goal": {"x": -0.028, "y": 0.546, "yaw": 2.73},
        "object": {"x": -0.44, "y": 0.727},
        "relative": {"forward_m": 1.53, "left_m": -0.85, "bearing_deg": -28.9}})
    out = locate_object.invoke({"description": "box", "state": dict(VOICE_STATE)})
    assert "x +1.53 m" in out
    assert "y -0.85 m" in out


def test_an_object_behind_reads_as_behind(monkeypatch):
    """Nothing in view is behind the robot, but ground_pixel is fed by TF and
    a stale/odd pose could produce it. Negative x must not print as "in front
    of me"."""
    _patch(monkeypatch, reply={
        "ok": True, "depth_m": 1.0,
        "goal": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "object": {"x": 0.0, "y": 0.0},
        "relative": {"forward_m": -1.20, "left_m": 0.30, "bearing_deg": 166.0}})
    out = locate_object.invoke({"description": "thing", "state": dict(VOICE_STATE)})
    assert "x -1.20 m" in out
    assert "1.20 m behind me" in out
    assert "in front of" not in out


def test_says_whose_left_it_means(monkeypatch):
    """A user facing the robot sees its left on their right, and read a
    correct answer as wrong because of it (2026-09-10). Say whose frame it is
    rather than expecting the reader to supply it."""
    _patch(monkeypatch)
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "robot's own" in out
    assert "swap if you are facing it" in out


def test_never_reports_odom_coordinates(monkeypatch):
    """odom's origin is wherever ./rover fused started, so its x/y say nothing
    about left or right — but printed next to forward/left, which do, they read
    as if they did. That misled a user on 2026-09-10 into thinking a correctly
    located wall was wrong. Robot-relative only."""
    _patch(monkeypatch)
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "odom" not in out.lower()
    assert "map position" not in out.lower()
    assert "-0.14" not in out and "-0.68" not in out


def test_distance_comes_from_relative_not_the_standoff_goal(monkeypatch):
    """`goal` is 0.45 m SHORT of the object on purpose; quoting it would
    understate every distance by that much, plausibly enough to go
    unnoticed. hypot(0.255, 0.463) = 0.53 — nothing like the real 1.48."""
    _patch(monkeypatch)
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "1.48 m away" in out
    assert "0.53 m away" not in out


def test_distance_is_measured_from_base_link_not_the_camera(monkeypatch):
    """forward_m already includes the camera's 0.17 m offset ahead of
    base_link, so the answer must come from `relative`, never from depth_m."""
    _patch(monkeypatch)
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "1.48 m away" in out
    assert "1.19 m away" not in out         # depth_m would give this


def test_right_hand_side_object_says_right(monkeypatch):
    _patch(monkeypatch, reply={
        "ok": True, "depth_m": 1.36,
        "goal": {"x": -0.028, "y": 0.546, "yaw": 2.73},
        "object": {"x": -0.44, "y": 0.727},
        "relative": {"forward_m": 1.53, "left_m": -0.85, "bearing_deg": -28.9}})
    out = locate_object.invoke({"description": "box", "state": dict(VOICE_STATE)})
    assert "slightly to my right" in out
    assert "0.85 m to my right" in out
    assert "odom" not in out.lower()


# ── It must never move ──────────────────────────────────────────────────────

def test_never_drives_and_never_turns(monkeypatch):
    """A question is not a command. approach_described_object rotates up to a
    full circle to find its target; asking "how far is it" must not."""
    _patch(monkeypatch)
    moved = []
    monkeypatch.setattr(_bridge.get(), "start_nav_to_pose",
                        lambda *a, **k: moved.append(("nav", a)))
    monkeypatch.setattr(_bridge.get(), "publish_twist",
                        lambda *a, **k: moved.append(("twist", a)))
    locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert moved == []


# ── Failure paths stay honest ───────────────────────────────────────────────

def test_not_in_view_says_so_and_offers_to_search(monkeypatch):
    _patch(monkeypatch, uv=None)
    out = locate_object.invoke({"description": "a cat", "state": dict(VOICE_STATE)})
    assert "can't see" in out
    assert "haven't turned" in out
    assert "m away" not in out

@pytest.mark.parametrize("reason, fragment", [
    ("no_depth_at_pixel", "no reading there"),
    ("depth_out_of_range", "usable range"),
    ("no_reply_from_jetson", "not answering"),
    ("no_robot_pose", "where I am"),
])
def test_depth_failures_are_distinguished_from_not_seeing_it(
        monkeypatch, reason, fragment):
    """"I can see it but can't measure it" is a different fact from "I can't
    see it", and the object IS there. Collapsing the two loses that."""
    _patch(monkeypatch, reply={"ok": False, "reason": reason})
    out = locate_object.invoke({"description": "mug", "state": dict(VOICE_STATE)})
    assert "I can see mug" in out
    assert fragment in out
    assert "m away" not in out


def test_unknown_depth_reason_still_refuses_to_guess(monkeypatch):
    _patch(monkeypatch, reply={"ok": False, "reason": "something_new"})
    out = locate_object.invoke({"description": "mug", "state": dict(VOICE_STATE)})
    assert "something_new" in out
    assert "m away" not in out


def test_old_jetson_without_relative_degrades_but_stays_truthful(monkeypatch):
    """Pre-2026-09-10 pixel_to_goal sends depth_m only. That is still a real
    measurement, so give it — but say it is from the camera, not from me."""
    _patch(monkeypatch, reply={"ok": True, "depth_m": 1.19,
                               "goal": {"x": 0.2, "y": 0.0, "yaw": 0.0}})
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "1.2 m" in out
    assert "from my camera" in out
    assert "older depth service" in out


def test_dead_camera_feed_admits_blindness(monkeypatch):
    monkeypatch.setattr(lo, "_fresh_frame", lambda b, settle_s=2.5: None)
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "can't measure" in out
    assert "m away" not in out


def test_vlm_error_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(lo, "_fresh_frame", lambda b, settle_s=2.5: b"jpeg")

    def _boom(frame, desc):
        raise RuntimeError("model down")

    monkeypatch.setattr(lo, "_vlm_locate", _boom)
    out = locate_object.invoke({"description": "chair", "state": dict(VOICE_STATE)})
    assert "RuntimeError" in out
    assert "m away" not in out
