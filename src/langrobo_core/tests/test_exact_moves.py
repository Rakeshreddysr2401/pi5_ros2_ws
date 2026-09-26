"""Exact moves (goal_exec) and grounding at the moment of the photo.

Rover repo INTELLIGENCE_PLAN.md B1 + B2, 2026-09-26. Off-robot: a scripted
bridge stands in for the Jetson's goal_exec replies and for pixel_to_goal.

B1: every turn and short move goes to goal_exec, which closes it on the fused
pose; the timed 5 rad/s twist is only the fallback when goal_exec is absent.
B2: a VLM pixel is grounded against the depth and camera pose of the PHOTO it
was picked from, not the newest ones 10-40 s later.
"""

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge
from langrobo_core.tools import movement
from langrobo_core.tools import approach as ap
from langrobo_core.tools.movement import move_robot

from test_movement import fake_twist, legs  # noqa: F401 -- fixtures


class ExactBridge(StubBridge):
    """goal_exec answers from a script: one reply per turn_by / drive_by."""

    def __init__(self, replies=None):
        super().__init__()
        self.replies = list(replies or [])
        self.calls = []

    def _next(self, kind, value):
        self.calls.append((kind, round(value, 1)))
        if self.replies:
            return dict(self.replies.pop(0))
        if kind == "turn":
            return {"ok": True, "result": "reached", "why": "", "turned_deg": value, "moved_cm": 4.0}
        return {"ok": True, "result": "reached", "why": "", "turned_deg": 0.0, "moved_cm": value * 100}

    def turn_by(self, degrees, timeout=40.0):
        return self._next("turn", degrees)

    def drive_by(self, metres, timeout=60.0):
        return self._next("drive", metres)


@pytest.fixture
def exact(monkeypatch):
    def make(replies=None):
        b = ExactBridge(replies)
        monkeypatch.setattr(_bridge, "_instance", b)
        return b
    yield make
    _bridge._instance = None
    _bridge.init(StubBridge())


# ── B1: move_robot on goal_exec ─────────────────────────────────────────────

def test_sequence_runs_on_goal_exec_and_reports_what_was_measured(exact, fake_twist, legs):
    b = exact()
    out = move_robot.invoke({"command": "F:60,L:90,F:30"})
    assert b.calls == [("drive", 0.6), ("turn", 90.0), ("drive", 0.3)]
    assert legs[0] == [], "no timed drive when goal_exec answers"
    assert out.startswith("Movement done: F:60, L:90, F:30")
    assert "turned +90 deg" in out and "moved 60 cm" in out
    assert "Timed" not in out


def test_right_turns_are_negative(exact, fake_twist, legs):
    b = exact()
    move_robot.invoke({"command": "R:45"})
    assert b.calls == [("turn", -45.0)]


def test_a_refused_step_stops_the_sequence_and_says_why(exact, fake_twist, legs):
    """Every later step was planned from where the refused one should have
    ended, so nothing after it may run."""
    b = exact([{"ok": True, "result": "reached", "why": "", "moved_cm": 60.0},
               {"ok": False, "result": "refused",
                "why": "something 0.26 m away is in the +90 deg swing",
                "turned_deg": 0.0, "moved_cm": 0.0}])
    out = move_robot.invoke({"command": "F:60,L:90,F:30"})
    assert len(b.calls) == 2
    assert "refused" in out and "0.26 m" in out
    assert "did NOT run: L:90, F:30" in out


def test_an_interrupt_during_an_exact_step_is_reported_as_one(exact, fake_twist, legs):
    exact([{"ok": False, "result": "interrupted", "why": "stopped by a new command"}])
    out = move_robot.invoke({"command": "L:90,F:30"})
    assert "interrupted during L:90" in out


def test_no_goal_exec_falls_back_to_timed_and_says_so(fake_twist, legs):
    """StubBridge answers 'unavailable', as the real bridge does when the
    Jetson's goal_exec is not running."""
    out = move_robot.invoke({"command": "L:90"})
    assert len(legs[0]) == 1, "the timed drive ran"
    assert "Timed and open-loop" in out


def test_a_big_turn_goes_in_pieces_aimed_on_the_measured_turn(exact, fake_twist, legs):
    """goal_exec turns the short way round, so 270 must not become -90. The
    second piece is aimed at what is LEFT by measurement, not by request."""
    b = exact([{"ok": True, "result": "reached", "why": "", "turned_deg": 168.0, "moved_cm": 5.0},
               {"ok": True, "result": "reached", "why": "", "turned_deg": 101.0, "moved_cm": 3.0}])
    out = move_robot.invoke({"command": "L:270"})
    assert b.calls == [("turn", 170.0), ("turn", 102.0)]
    assert "turned +269 deg" in out and "slid 8 cm" in out


def test_a_long_drive_goes_in_pieces(exact, fake_twist, legs):
    b = exact()
    move_robot.invoke({"command": "F:250"})
    assert b.calls == [("drive", 1.0), ("drive", 1.0), ("drive", 0.5)]


def test_turn_robot_uses_goal_exec_when_it_is_there(exact, fake_twist, legs):
    b = exact()
    assert movement.turn_robot(b, 90.0) == (True, "")
    assert legs[0] == []


def test_turn_robot_passes_a_refusal_through(exact, fake_twist, legs):
    b = exact([{"ok": False, "result": "stalled", "why": "turn stuck +23.0 deg from target"}])
    ok, why = movement.turn_robot(b, 90.0)
    assert not ok and "stalled" in why and "23" in why


# ── B2: grounding at the moment of the photo ───────────────────────────────

class GroundBridge(StubBridge):
    def __init__(self, replies, pose_then=(0.0, 0.0, 0.0), pose_now=(0.0, 0.0, 0.0)):
        super().__init__()
        self.replies, self.queries, self.held = list(replies), [], []
        self.pose = pose_then
        self.pose_now = pose_now

    def ground_pixel(self, u, v, timeout=4.0, stamp=None, box=None):
        self.queries.append(stamp)
        self.boxes = getattr(self, "boxes", []) + [box]
        return self.replies.pop(0)

    def get_current_pose(self):
        return self.pose_now


def test_the_query_carries_the_photo_stamp():
    b = GroundBridge([{"ok": True}])
    ap._ground(b, (10.0, 20.0), {"stamp": (1790425228, 523979492), "pose": (0, 0, 0)})
    assert b.queries == [(1790425228, 523979492)]


def test_expired_photo_while_still_is_regrounded_on_the_newest_view():
    """Same place, same heading: the newest depth IS the photo's view."""
    b = GroundBridge([{"ok": False, "reason": "snapshot_expired"}, {"ok": True}],
                     pose_now=(0.005, 0.0, 0.3))
    res = ap._ground(b, (1.0, 1.0), {"stamp": (5, 0), "pose": (0.0, 0.0, 0.0)})
    assert res["ok"] and b.queries == [(5, 0), None]


def test_expired_photo_after_moving_is_not_regrounded():
    """It turned 30 deg since the photo: the newest depth is another view,
    and grounding the old pixel on it is exactly the bug B2 removes."""
    b = GroundBridge([{"ok": False, "reason": "snapshot_expired"}],
                     pose_now=(0.0, 0.0, 30.0))
    res = ap._ground(b, (1.0, 1.0), {"stamp": (5, 0), "pose": (0.0, 0.0, 0.0)})
    assert not res["ok"] and b.queries == [(5, 0)]


def test_other_failures_are_not_regrounded():
    b = GroundBridge([{"ok": False, "reason": "no_depth_at_pixel"}])
    ap._ground(b, (1.0, 1.0), {"stamp": (5, 0), "pose": (0.0, 0.0, 0.0)})
    assert b.queries == [(5, 0)]


def test_a_photo_without_a_stamp_grounds_the_old_way():
    b = GroundBridge([{"ok": True}])
    ap._ground(b, (1.0, 1.0), {"stamp": None, "pose": None})
    assert b.queries == [None]


def test_capture_holds_the_photo_on_the_jetson():
    class B(StubBridge):
        held = []

        def get_frame_stamped(self, max_age_s=None):
            return b"jpeg", (7, 8)

        def hold_frame(self, stamp):
            self.held.append(stamp)

    b = B()
    frame, cap = ap._capture(b)
    assert frame == b"jpeg" and cap["stamp"] == (7, 8) and b.held == [(7, 8)]
    assert cap["pose"] == (0.0, 0.0, 0.0)


# ── the VLM's box, not one pixel ────────────────────────────────────────────
# Floor test 2026-09-26: Gemma's y is off by up to ~45 px, so its "centre" of
# a bottle 1 m away sat on the cap's top edge and the depth there was the door.

def test_the_box_reaches_the_jetson_query():
    b = GroundBridge([{"ok": True}])
    ap._ground(b, (257.0, 203.0, (240.0, 167.0, 274.0, 239.0)), {"stamp": (5, 0), "pose": None})
    assert b.boxes == [(240.0, 167.0, 274.0, 239.0)]


def test_a_point_only_answer_still_grounds():
    b = GroundBridge([{"ok": True}])
    ap._ground(b, (257.0, 203.0), {"stamp": None, "pose": None})
    assert b.boxes == [None]


class _Reply:
    def __init__(self, text):
        self.content = text


def _vlm_says(monkeypatch, text):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (896, 504)).save(buf, "JPEG")
    from langrobo_core.services import llm
    monkeypatch.setattr(llm, "get_llm", lambda slot: type("L", (), {"invoke": lambda self, m: _Reply(text)})())
    return ap._vlm_locate(buf.getvalue(), "the orange bottle")


def test_vlm_box_becomes_pixels_and_a_centre(monkeypatch):
    """The live reply, fenced as Gemma sends it: [ymin, xmin, ymax, xmax] /1000."""
    u, v, box = _vlm_says(monkeypatch, '```json\n{"found": true, "box": [331, 268, 475, 306]}\n```')
    assert box == pytest.approx((240.1, 166.8, 274.2, 239.4), abs=0.1)
    assert (u, v) == pytest.approx((257.2, 203.1), abs=0.1)


def test_vlm_point_only_is_still_accepted(monkeypatch):
    u, v, box = _vlm_says(monkeypatch, '{"found": true, "x": 500, "y": 500}')
    assert (u, v, box) == (448.0, 252.0, None)


def test_vlm_nonsense_box_falls_back_or_refuses(monkeypatch):
    assert _vlm_says(monkeypatch, '{"found": true, "box": [500, 600, 400, 100]}') is None
    assert _vlm_says(monkeypatch, '{"found": false}') is None
