"""scan_surroundings: N views a full circle apart, then ONE description of the room
(real robot 2026-10-18: "look around and tell me what is in this room" was
answered from one forward photo; a scan ended with "I took 6 photos")."""
import langrobo_core.tools._bridge as _bridge
import langrobo_core.tools.approach as ap
import langrobo_core.tools.movement as mv
import pytest
from langrobo_core.bridges.stub import StubBridge


pytestmark = pytest.mark.usefixtures("fake_twist")

_bridge._instance = None
_bridge.init(StubBridge())


def _rig(monkeypatch, turn_ok=lambda i: True):
    turns, shots, asked = [], [], []
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: False)

    def turn(bridge, deg):
        turns.append(deg)
        return (True, "") if turn_ok(len(turns)) else (False, "blocked on the left")
    monkeypatch.setattr(ap._mv, "turn_robot", turn)
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)

    def capture(bridge, settle_s=1.0, source="scan"):
        shots.append(source)
        return b"jpeg%d" % len(shots), {"stamp": None, "pose": None, "when": 0}
    monkeypatch.setattr(ap, "_capture", capture)

    def describe(views):
        asked.append([d for _, d in views])
        return "A sofa ahead, a door on the left."
    monkeypatch.setattr(ap, "describe_scan", describe)
    return turns, shots, asked


def test_full_circle_then_one_description(monkeypatch):
    turns, shots, asked = _rig(monkeypatch)
    out = ap.scan_surroundings.invoke({})
    assert turns == [360.0 / ap.SCAN_VIEWS] * ap.SCAN_VIEWS          # exactly a full circle
    assert len(shots) == ap.SCAN_VIEWS                                # the start view + one per stop, not the start twice
    assert asked == [[i * 360.0 / ap.SCAN_VIEWS for i in range(ap.SCAN_VIEWS)]]
    assert "sofa ahead" in out and "door on the left" in out


def test_map_only_scan_asks_no_model(monkeypatch):
    turns, shots, asked = _rig(monkeypatch)
    out = ap.scan_surroundings.invoke({"describe": False})
    assert asked == [] and "Scan complete" in out


def test_blocked_half_way_still_describes_what_it_saw(monkeypatch):
    turns, shots, asked = _rig(monkeypatch, turn_ok=lambda n: n < 3)
    out = ap.scan_surroundings.invoke({})
    assert "incomplete" in out and "sofa ahead" in out
    assert asked == [[0.0, 360.0 / ap.SCAN_VIEWS, 2 * 360.0 / ap.SCAN_VIEWS]]   # the start view + the 2 stops it reached


def test_cannot_turn_at_all_is_said_plainly(monkeypatch):
    _rig(monkeypatch, turn_ok=lambda n: False)
    out = ap.scan_surroundings.invoke({})
    assert "could NOT look around" in out


def test_direction_words():
    d = ap._direction
    assert d(0) == "ahead" and d(360) == "ahead" and d(180) == "behind"
    assert d(90) == "left" and d(-90) == "right" and d(270) == "right"
    assert d(60) == "ahead-left" and d(130) == "behind-left" and d(-130) == "behind-right"
