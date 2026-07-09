"""tools/movement.py — point_camera clamping + registration (off-robot, StubBridge)."""

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge, NAVIGATE_TOOLS
from langrobo_core.tools.movement import point_camera

_bridge._instance = None
_bridge.init(StubBridge())


def test_navigate_has_point_camera():
    names = [t.name for t in NAVIGATE_TOOLS]
    assert "point_camera" in names


def test_point_camera_in_range():
    result = point_camera.invoke({"pan_deg": 45, "tilt_deg": 10})
    assert "45" in result and "10" in result
    assert "clamped" not in result.lower()


def test_point_camera_clamps_out_of_range():
    result = point_camera.invoke({"pan_deg": 150, "tilt_deg": -999})
    assert "clamped" in result.lower()
    assert "90" in result and "-30" in result


def test_point_camera_defaults_center():
    result = point_camera.invoke({})
    assert "pan=0" in result and "tilt=0" in result
