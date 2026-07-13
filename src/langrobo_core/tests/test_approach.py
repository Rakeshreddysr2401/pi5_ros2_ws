"""tools/approach.py — standoff geometry + approach flow on the StubBridge."""

import math

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge, NAVIGATE_TOOLS
from langrobo_core.tools.approach import (approach_object, compute_standoff_goal,
                                          list_saved_locations)

import langrobo_core.tools.approach as ap

# The search behaviour sleeps real seconds (servo settle, detector dwell,
# base rotation) — collapse it so the suite stays ~4s. Geometry and flow are
# unchanged; only the waits shrink.
ap._PAN_DWELL_S = 0.02
ap._BASE_DWELL_S = 0.02
ap._BASE_STEPS = 1
import langrobo_core.tools.movement as mv
mv._RECENTER_SETTLE_S = 0.01

VOICE_STATE = {"channel": "voice", "sender_name": "voice", "messages": []}


def fresh_bridge(**kw):
    _bridge._instance = None
    b = StubBridge(**kw)
    _bridge.init(b)
    return b


# ── Registration ──────────────────────────────────────────────────────────────

def test_navigate_has_new_tools():
    names = [t.name for t in NAVIGATE_TOOLS]
    for tool_name in ("approach_object", "scan_surroundings", "list_saved_locations"):
        assert tool_name in names


# ── Standoff geometry (pure) ──────────────────────────────────────────────────

def test_standoff_goal_straight_line():
    gx, gy, yaw = compute_standoff_goal(0.0, 0.0, 2.0, 0.0, standoff=0.5)
    assert math.isclose(gx, 1.5) and math.isclose(gy, 0.0)
    assert math.isclose(yaw, 0.0)


def test_standoff_goal_diagonal_faces_object():
    gx, gy, yaw = compute_standoff_goal(0.0, 0.0, 3.0, 4.0, standoff=1.0)
    # goal sits 1m short of the object on the 3-4-5 line
    assert math.isclose(math.hypot(3.0 - gx, 4.0 - gy), 1.0, abs_tol=1e-6)
    assert math.isclose(yaw, math.degrees(math.atan2(4, 3)), abs_tol=1e-6)


def test_standoff_goal_already_close_turns_in_place():
    gx, gy, yaw = compute_standoff_goal(1.0, 1.0, 1.2, 1.0, standoff=0.5)
    assert (gx, gy) == (1.0, 1.0)      # stay put
    assert math.isclose(yaw, 0.0)      # but face the object


# ── Approach flow ─────────────────────────────────────────────────────────────

def test_approach_with_fresh_detection_starts_nav():
    bridge = fresh_bridge()
    bridge.detections = {"chair": {"x": 2.0, "y": 0.0, "z": 0.3,
                                   "conf": 0.8, "age_s": 0.2}}
    result = approach_object.invoke({"target": "chair", "state": dict(VOICE_STATE)})
    assert "on my way" in result.lower()


def test_approach_uses_last_seen_when_stale():
    bridge = fresh_bridge()
    # Seen 60s ago — not fresh, but remembered. StubBridge has no wheels, so
    # the search comes up empty and the world-model fallback should kick in.
    bridge.detections = {"tv": {"x": 1.0, "y": 1.0, "z": 0.5,
                                "conf": 0.7, "age_s": 60.0}}
    result = approach_object.invoke({"target": "tv", "state": dict(VOICE_STATE)})
    assert "on my way" in result.lower()


def test_approach_unseen_object_is_honest():
    fresh_bridge()
    result = approach_object.invoke({"target": "elephant", "state": dict(VOICE_STATE)})
    assert "couldn't find" in result.lower()


def test_approach_no_localisation_is_honest():
    bridge = fresh_bridge()
    bridge.detections = {"person": {"x": 1.0, "y": 0.0, "z": 0.4,
                                    "conf": 0.9, "age_s": 0.1}}
    bridge.get_current_pose = lambda: None
    result = approach_object.invoke({"target": "person", "state": dict(VOICE_STATE)})
    assert "localisation" in result.lower()


def test_list_saved_locations():
    fresh_bridge(known_locations={"kitchen": (1, 2, 0), "dock": (0, 0, 0)})
    result = list_saved_locations.invoke({})
    assert "kitchen" in result and "dock" in result
