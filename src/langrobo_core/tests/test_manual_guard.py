"""The teleop MANUAL guard — refusing to promise a drive that cannot happen.

teleop_web.py in MANUAL publishes zeros to /cmd_vel at 10 Hz, which cancel
everything Nav2 sends. The rover sits still, Nav2 aborts, and it presents as a
planner or controller fault. On 2026-09-10 a user left the switch in MANUAL,
was told "On my way", and nothing moved — then the model claimed it had put
itself back into automatic mode, which no tool can do.

So the properties here are: don't promise, don't drive, don't claim to have
fixed the switch, and don't ground the robot when the switch merely can't be
read.
"""

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge
from langrobo_core.tools.approach import approach_described_object
from langrobo_core.tools.movement import blocked_by_manual, navigate_to_pose
import langrobo_core.tools.approach as ap
import langrobo_core.tools.movement as mv

_bridge._instance = None
_bridge.init(StubBridge())

VOICE_STATE = {"channel": "voice", "sender_name": "voice", "messages": []}


# ── The reader ──────────────────────────────────────────────────────────────

def test_manual_blocks(monkeypatch):
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: True)
    assert blocked_by_manual() is not None


def test_auto_does_not_block(monkeypatch):
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: False)
    assert blocked_by_manual() is None


def test_unreadable_teleop_does_not_block(monkeypatch):
    """A teleop that is down publishes no zeros, so it is not the failure this
    guard exists for. Refusing then would ground the robot for a reason that
    is not true — "can't read the switch" must not become "the switch is on"."""
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: None)
    assert blocked_by_manual() is None


def test_reader_never_raises_and_never_hangs(monkeypatch):
    """Called on the conversation's own path, so a dead teleop must degrade to
    None rather than propagate — a network error here would surface to the
    user as a vision or navigation fault."""
    def _boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert mv.teleop_is_manual() is None


# ── The refusal message ─────────────────────────────────────────────────────

def test_refusal_says_who_can_fix_it(monkeypatch):
    """It must not imply the robot can flip the switch: the model claimed
    exactly that on 2026-09-10 and the user believed it."""
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: True)
    msg = blocked_by_manual()
    assert "MANUAL" in msg
    assert "cannot change the switch myself" in msg
    assert "AUTO" in msg


# ── Both driving tools honour it ────────────────────────────────────────────

def test_navigate_to_pose_refuses_in_manual(monkeypatch):
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: True)
    started = []
    monkeypatch.setattr(_bridge.get(), "start_nav_to_pose",
                        lambda *a, **k: started.append(a))
    out = navigate_to_pose.invoke({"location": "kitchen",
                                   "state": dict(VOICE_STATE)})
    assert "MANUAL" in out
    assert started == [], "no goal may be sent while teleop is zeroing the wheels"


def test_approach_refuses_in_manual_before_paying_for_the_vlm(monkeypatch):
    """The search costs a VLM round trip per step AND rotates the base. Both
    are wasted if the wheels are being zeroed, so the guard runs first."""
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: True)
    called = []
    monkeypatch.setattr(ap, "_fresh_frame",
                        lambda b, settle_s=2.5: called.append("frame") or b"jpeg")
    monkeypatch.setattr(ap, "_vlm_locate",
                        lambda f, d: called.append("vlm") or (10.0, 10.0))
    started = []
    monkeypatch.setattr(_bridge.get(), "start_nav_to_pose",
                        lambda *a, **k: started.append(a))
    out = approach_described_object.invoke(
        {"description": "white bucket", "state": dict(VOICE_STATE)})
    assert "MANUAL" in out
    assert called == [], "no camera or VLM work before the guard"
    assert started == []


def test_approach_proceeds_normally_in_auto(monkeypatch):
    """The guard must not become a second way for approach to fail."""
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: False)
    monkeypatch.setattr(ap, "_fresh_frame", lambda b, settle_s=2.5: b"jpeg")
    monkeypatch.setattr(ap, "_vlm_locate", lambda f, d: (100.0, 50.0))
    monkeypatch.setattr(_bridge.get(), "ground_pixel",
                        lambda u, v, timeout=4.0: {
                            "ok": True, "depth_m": 1.8,
                            "goal": {"x": 1.2, "y": 0.3, "yaw": 0.0}})
    started = []
    monkeypatch.setattr(_bridge.get(), "start_nav_to_pose",
                        lambda *a, **k: started.append(a))
    out = approach_described_object.invoke(
        {"description": "white bucket", "state": dict(VOICE_STATE)})
    assert "MANUAL" not in out
    assert started, "a Nav2 goal should have been sent"
