"""The room search (2026-10-13, owner: "when it's still not found it needs to
go to another place and find it -- whole room search one by one"): after a
full circle finds nothing, approach drives to an open, not-yet-searched spot
(utils/viewpoints.py, from the LiDAR) and looks all around there.
"""

import pytest

from langrobo_core.tools.approach import approach_described_object
from langrobo_core.utils import viewpoints as vp

from test_object_memory import STATE, _grounded, robot, scripted, fake_twist  # noqa: F401


def _room(w=4.0, h=3.0, step=0.1):
    """The walls of a w x h room with its corner at (0, 0)."""
    pts = [(i * step, 0.0) for i in range(int(w / step) + 1)]
    pts += [(i * step, h) for i in range(int(w / step) + 1)]
    pts += [(0.0, j * step) for j in range(int(h / step) + 1)]
    pts += [(w, j * step) for j in range(int(h / step) + 1)]
    return pts


def test_picks_an_open_new_spot_inside_the_room():
    here = (0.8, 0.8, 0.0)
    x, y, yaw = vp.next_viewpoint(here, _room(), [here[:2]])
    assert 0.5 < x < 3.5 and 0.5 < y < 2.5, (x, y)              # inside, off the walls
    assert ((x - 0.8) ** 2 + (y - 0.8) ** 2) ** 0.5 >= vp.NEW_M


def test_never_the_same_place_twice_and_none_when_all_seen():
    here = (0.8, 0.8, 0.0)
    first = vp.next_viewpoint(here, _room(), [here[:2]])
    second = vp.next_viewpoint(first, _room(), [here[:2], first[:2]])
    assert second is not None
    for a in ((0.8, 0.8), first[:2]):
        assert ((second[0] - a[0]) ** 2 + (second[1] - a[1]) ** 2) ** 0.5 >= vp.NEW_M
    # a cupboard-sized space: nowhere new to go
    assert vp.next_viewpoint((0.5, 0.5, 0.0), _room(1.0, 1.0), [(0.5, 0.5)]) is None


def test_its_own_body_in_the_scan_is_not_a_wall():
    body = [(0.15, 0.0), (0.0, 0.15), (-0.15, 0.0), (0.0, -0.15)]
    assert vp.next_viewpoint((2.0, 1.5, 0.0), _room() + [(2.0 + a, 1.5 + b) for a, b in body],
                             [(2.0, 1.5)]) is not None


def test_no_scan_no_move():
    assert vp.next_viewpoint((1.0, 1.0, 0.0), None, []) is None


def test_not_found_here_found_from_the_next_spot(robot, scripted):
    turns, vlm = scripted
    robot.pose = (0.8, 0.8, 0.0)
    robot.get_scan_points = lambda: _room()
    robot.legs = [{"ok": True, "result": "reached"}]
    vlm.extend([None] * 8 + [None, (448.0, 250.0, None)])      # a whole circle, then the 2nd view there
    robot.replies = [_grounded(3.0, 2.0)]
    out = approach_described_object.invoke({"description": "red ball", "state": dict(STATE)})
    assert len(robot.leg_goals) == 1 and robot.navs, out
    assert "On my way" in out


def test_says_how_many_spots_it_searched(robot, scripted):
    robot.pose = (0.8, 0.8, 0.0)
    robot.get_scan_points = lambda: _room()
    robot.legs = [{"ok": True, "result": "reached"}] * 3
    out = approach_described_object.invoke({"description": "red ball", "state": dict(STATE)})
    assert "searched 3 spots" in out and not robot.navs, out
    assert len(robot.leg_goals) == 2


def test_a_spot_it_cannot_reach_is_skipped(robot, scripted):
    robot.pose = (0.8, 0.8, 0.0)
    robot.get_scan_points = lambda: _room()
    robot.legs = [{"ok": False, "result": "failed", "why": "no path"},
                  {"ok": True, "result": "reached"}, {"ok": True, "result": "reached"}]
    out = approach_described_object.invoke({"description": "red ball", "state": dict(STATE)})
    assert "searched 3 spots" in out, out
    assert len({g[:2] for g in robot.leg_goals}) == 3       # never the failed spot again
