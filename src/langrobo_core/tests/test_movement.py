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


def test_point_camera_is_not_bound_without_a_head():
    """No servos fitted → the tool is not in any agent's set at all.

    An always-refusing tool still costs prompt tokens on every turn and still
    tempts the model into calling it. It comes back for both agents at once
    when LANGROBO_PAN_TILT=1."""
    from langrobo_core.tools import HEAD_TOOLS
    assert not movement.PAN_TILT_ENABLED
    assert HEAD_TOOLS == []
    assert "point_camera" not in [t.name for t in NAVIGATE_TOOLS]


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


# ══════════════════════════════════════════════════════════════════════════════
# move_robot — sequences, validation, and honest partial reports
#
# The bug these pin down: asked to "move 60 cm front and take left then move
# 30 cm", the robot ran F:60 and stopped, and the reply was "I have moved
# forward sixty centimeters" — true about step 1, silent about the two steps
# that never happened. Nothing in the system knew there were three.
# ══════════════════════════════════════════════════════════════════════════════

from langrobo_core.tools.movement import move_robot, _parse_step, _label


@pytest.fixture
def fake_twist(monkeypatch):
    """move_robot imports geometry_msgs at call time and ROS is not on the path
    off-robot, so stand one in. Twist needs only the fields the tool sets."""
    import sys
    import types
    class Twist:
        def __init__(self):
            self.linear = types.SimpleNamespace(x=0.0, y=0.0, z=0.0)
            self.angular = types.SimpleNamespace(x=0.0, y=0.0, z=0.0)
    mod = types.ModuleType("geometry_msgs")
    msg = types.ModuleType("geometry_msgs.msg")
    msg.Twist = Twist
    mod.msg = msg
    monkeypatch.setitem(sys.modules, "geometry_msgs", mod)
    monkeypatch.setitem(sys.modules, "geometry_msgs.msg", msg)
    return Twist


@pytest.fixture
def legs(monkeypatch):
    """Record each timed drive instead of running it — no real sleeps, and the
    test decides which leg gets interrupted."""
    calls = []
    plan = {"fail_at": None}          # 1-based index of the leg that is cut short
    def fake_drive(bridge, twist, dur):
        calls.append(dur)
        return not (plan["fail_at"] == len(calls))
    monkeypatch.setattr(movement, "_drive_for_duration", fake_drive)
    return calls, plan


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parse_step_reads_a_normal_command():
    assert _parse_step("F:60") == ("F", 60.0)
    assert _parse_step(" l:90 ") == ("L", 90.0)
    assert _parse_step("S") == ("S", 0.0)


@pytest.mark.parametrize("bad", ["X:10", "F", "F:", "F:abc"])
def test_parse_step_rejects_what_used_to_pass_silently(bad):
    # An unknown direction left the Twist at zero and still returned
    # "Movement done" — a move that never happened, reported as success.
    with pytest.raises(ValueError):
        _parse_step(bad)


def test_parse_step_rejects_negatives():
    # A negative made _duration() negative, which skipped the timed drive and
    # published a twist nothing ever stopped: the wheels ran until the 500 ms
    # watchdog caught them.
    with pytest.raises(ValueError):
        _parse_step("F:-20")


def test_label_is_what_the_user_sees():
    assert _label("F", 60.0) == "F:60"
    assert _label("L", 90.5) == "L:90.5"
    assert _label("S", 0.0) == "S"


# ── nothing moves unless the whole sequence is valid ─────────────────────────

def test_a_bad_step_moves_nothing_at_all(fake_twist, legs):
    calls, _ = legs
    out = move_robot.invoke({"command": "F:60,X:90,F:30"})
    assert "Nothing was moved" in out
    assert calls == [], "a bad step 2 must not leave the robot moved by step 1"


def test_empty_command_moves_nothing(fake_twist, legs):
    calls, _ = legs
    assert "No movement command" in move_robot.invoke({"command": "  "})
    assert calls == []


def test_too_many_steps_moves_nothing(fake_twist, legs):
    calls, _ = legs
    out = move_robot.invoke({"command": ",".join(["F:10"] * 9)})
    assert "Too many steps" in out and "Nothing was moved" in out
    assert calls == []


# ── the actual fix ───────────────────────────────────────────────────────────

def test_one_call_runs_every_step_in_order(fake_twist, legs):
    calls, _ = legs
    out = move_robot.invoke({"command": "F:60,L:90,F:30"})
    assert out.startswith("Movement done: F:60, L:90, F:30")
    assert "call look()" in out, "a move must tell the model its view is stale"
    assert len(calls) == 3, "all three legs must actually run"


def test_single_command_still_reads_the_same(fake_twist, legs):
    assert move_robot.invoke({"command": "F:60"}).startswith("Movement done: F:60")


def test_interruption_names_the_steps_that_did_not_run(fake_twist, legs):
    calls, plan = legs
    plan["fail_at"] = 2                       # cut the robot off during the turn
    out = move_robot.invoke({"command": "F:60,L:90,F:30"})
    assert "completed: F:60" in out
    assert "did NOT run: L:90, F:30" in out   # <- the whole point
    assert "Movement done" not in out
    assert len(calls) == 2, "must stop, not carry on to step 3"


def test_interruption_on_the_first_step_says_nothing_completed(fake_twist, legs):
    calls, plan = legs
    plan["fail_at"] = 1
    out = move_robot.invoke({"command": "F:60,L:90"})
    assert "completed: nothing" in out
    assert "did NOT run: F:60, L:90" in out


def test_stop_in_a_sequence_abandons_the_rest(fake_twist, legs):
    calls, _ = legs
    out = move_robot.invoke({"command": "F:60,S,F:30"})
    assert "did NOT run: F:30" in out
    assert len(calls) == 1, "'S' means stop — the third leg must never run"


# ── The camera view goes stale when the base moves ──────────────────────────
#
# look() leaves the frame in the conversation labelled "[Current camera view]"
# for good, and local_agent keeps images, so a question asked after a drive was
# answered from a photo of somewhere the robot had left. The history cannot be
# edited to fix it -- message_utils' projection is append-only on purpose -- so
# the movement result has to carry the news itself.

def test_a_move_says_the_view_is_now_stale(fake_twist, legs):
    out = move_robot.invoke({"command": "F:30"})
    assert "MOVED" in out and "call look()" in out


def test_a_turn_says_it_too(fake_twist, legs):
    out = move_robot.invoke({"command": "L:180"})
    assert "call look()" in out


def test_a_bare_stop_does_not_claim_the_view_changed(fake_twist, legs):
    """'S' moves nothing, so the frame in the conversation is still valid.
    Crying stale here would burn a 10-40 s look for no reason."""
    out = move_robot.invoke({"command": "S"})
    assert "call look()" not in out


def test_a_partial_sequence_still_warns_about_what_did_run(fake_twist, legs):
    """Interrupted DURING the first leg: the base still drove part of the way,
    so the view is stale even though "completed: nothing"."""
    calls, cfg = legs
    cfg["fail_at"] = 1
    out = move_robot.invoke({"command": "F:60,L:90,F:30"})
    assert "did NOT run" in out
    assert "call look()" in out
