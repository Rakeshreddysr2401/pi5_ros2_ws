"""tools/movement.py — point_camera clamping + registration (off-robot, StubBridge)."""

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge, NAVIGATE_TOOLS
from langrobo_core.tools import movement
from langrobo_core.tools.movement import point_camera

_bridge._instance = None
_bridge.init(StubBridge())


@pytest.fixture
def head_fitted(monkeypatch):
    """Pretend the pan/tilt servos exist — the clamping geometry below is what
    runs once they do. Without this the tool correctly refuses instead (see
    PAN_TILT_ENABLED: nothing on the ESP32 subscribes to /servo_pan)."""
    monkeypatch.setattr(movement, "PAN_TILT_ENABLED", True)


def test_navigate_has_point_camera():
    names = [t.name for t in NAVIGATE_TOOLS]
    assert "point_camera" in names


def test_point_camera_in_range(head_fitted):
    result = point_camera.invoke({"pan_deg": 45, "tilt_deg": 10})
    assert "45" in result and "10" in result
    assert "clamped" not in result.lower()


def test_point_camera_clamps_out_of_range(head_fitted):
    result = point_camera.invoke({"pan_deg": 150, "tilt_deg": -999})
    assert "clamped" in result.lower()
    assert "90" in result and "-30" in result


def test_point_camera_defaults_center(head_fitted):
    result = point_camera.invoke({})
    assert "pan=0" in result and "tilt=0" in result


def test_point_camera_admits_no_mount():
    """Default build has no servos: the tool must say so, not report success.

    It used to return "Camera pointed: pan=60°, tilt=0°" while nothing moved —
    the model then told the user it had looked left."""
    assert not movement.PAN_TILT_ENABLED
    result = point_camera.invoke({"pan_deg": 60.0, "tilt_deg": 0.0})
    assert "pan=" not in result
    assert "mount" in result.lower()


def test_ensure_head_centred_is_free_without_a_head(monkeypatch):
    """No servos → no re-centre, and above all no 0.8s sleep. That sleep sat in
    front of every navigate_to_pose, save_location and approach_object."""
    called = []
    monkeypatch.setattr(movement.time, "sleep", lambda s: called.append(s))

    class _Panned:
        def get_pan_tilt(self):
            return (55.0, 0.0)

        def set_pan_tilt(self, pan, tilt):
            called.append(("set", pan, tilt))

    movement.ensure_head_centred(_Panned())
    assert called == []


# ── Drive calibration ───────────────────────────────────────────────────────
# rover_firmware_v2.ino closed-loop PID-tracks /cmd_vel in SI m/s, so the speed
# used to compute a drive DURATION must be the speed actually commanded. They
# disagreed (command 0.28 m/s, assumed physical 0.60 m/s) and every distance
# came out 2.1x short: move_robot("F:20") drove ~9 cm.

def test_forward_duration_covers_the_distance_asked_for():
    for cm in (10.0, 20.0, 55.0):
        travelled_m = movement._duration("F", cm) * movement._LINEAR_VEL_MS
        assert travelled_m == pytest.approx(cm / 100.0, rel=0.02), (
            f"F:{cm:.0f} drives {travelled_m * 100:.1f} cm — _PHYSICAL_VEL_MS "
            f"({movement._PHYSICAL_VEL_MS}) disagrees with the commanded "
            f"_LINEAR_VEL_MS ({movement._LINEAR_VEL_MS})")


def test_turn_duration_covers_the_angle_asked_for():
    import math
    for deg in (45.0, 90.0, 180.0):
        turned = math.degrees(
            movement._duration("L", deg) * movement._ANGULAR_VEL_RS)
        assert turned == pytest.approx(deg, rel=0.02), (
            f"L:{deg:.0f} turns {turned:.1f}° — _STEADY_STATE_ANGULAR_VEL "
            f"disagrees with the commanded _ANGULAR_VEL_RS")


def test_commanded_speeds_stay_inside_the_chassis_envelope():
    """Caps come from the rover repo's own measurements, not from taste.

    Linear: OPERATIONS.md §2 drives 0.20 m/s and calls it "under the tracking
    limit". Angular: full authority is 2 * MAX_WHEEL_VEL / WHEEL_BASE_M
    = 2 * 0.86 / 0.34 = 5.06 rad/s (teleop_web.py), above which the PID simply
    saturates and the commanded yaw stops meaning anything."""
    assert 0 < movement._LINEAR_VEL_MS <= 0.22
    assert 0 < movement._ANGULAR_VEL_RS <= 5.06
