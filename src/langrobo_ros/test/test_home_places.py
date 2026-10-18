"""Saved places survive a power cycle through the room's map frame (2026-10-18).

    cd src/langrobo_ros && python3 -m pytest test/test_home_places.py -q

odom restarts at 0 every boot, so a place stamped with last boot's odom epoch
was dropped. When the Jetson announces the saved room (/home/room) the place is
ALSO kept in that room's map frame, and comes back next boot -- re-projected
into the new odom through map -> odom -- if and only if it is the same room.
Run on a stand-in `self` (no ROS graph), as test_follow_bridge.py does.
"""
import json
import math
import types

import pytest

from langrobo_ros.ros2_bridge import ROS2Bridge


def bridge(tmp_path, epoch, room, map_in_odom):
    """map_in_odom: (x, y, yaw_deg) of the map frame expressed in odom (this boot's SLAM correction)."""
    s = types.SimpleNamespace()
    s.NAV_FRAME = "odom"
    s._locations_file = str(tmp_path / "locations.json")
    s._config_locations, s._known_locations, s._saved_locations = {}, {}, {}
    s._origin_epoch, s._warned_stale_locations = epoch, False
    s._room = {"id": room, "frame": "map", "state": "resumed"} if room else None
    s._node = types.SimpleNamespace(get_logger=lambda: types.SimpleNamespace(info=print, warning=print))
    mx, my, myaw = map_in_odom

    def tf_pose(parent, child):
        if (parent, child) == ("odom", "map"):
            return map_in_odom
        if (parent, child) == ("map", "odom"):           # the inverse
            c, sn = math.cos(math.radians(-myaw)), math.sin(math.radians(-myaw))
            return (-(c * mx - sn * my), -(sn * mx + c * my), -myaw)
        return None
    s._tf_pose = tf_pose
    for name in ("_map_to_odom", "_odom_to_map", "add_known_location", "get_known_locations",
                 "_rebuild_known_locations", "_on_home_room"):
        setattr(s, name, types.MethodType(getattr(ROS2Bridge, name), s))
    return s


def load(s):
    with open(s._locations_file) as f:
        raw = json.load(f)
    s._saved_locations = {k: tuple(v) if len(v) in (4, 5) else (*v, None) for k, v in raw.items()}
    s._rebuild_known_locations()


def test_a_place_comes_back_next_boot_in_the_same_room(tmp_path):
    # boot 1: map and odom coincide; save "kitchen" at odom (2, 1) facing 90 deg
    b1 = bridge(tmp_path, epoch=111, room="r1", map_in_odom=(0.0, 0.0, 0.0))
    b1.add_known_location("kitchen", 2.0, 1.0, 90.0)
    # boot 2: odom restarted elsewhere: the map frame now sits at (1, -0.5) turned 30 deg in odom
    b2 = bridge(tmp_path, epoch=222, room="r1", map_in_odom=(1.0, -0.5, 30.0))
    load(b2)
    assert "kitchen" not in b2._known_locations          # the old odom numbers are NOT served
    got = b2.get_known_locations()["kitchen"]            # ... the room's map frame brings it back
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    want = (1.0 + c * 2.0 - s * 1.0, -0.5 + s * 2.0 + c * 1.0, 120.0)
    assert got == pytest.approx(want, abs=1e-6)


def test_another_room_does_not_get_it(tmp_path):
    b1 = bridge(tmp_path, epoch=111, room="r1", map_in_odom=(0.0, 0.0, 0.0))
    b1.add_known_location("kitchen", 2.0, 1.0, 90.0)
    b2 = bridge(tmp_path, epoch=222, room="r2", map_in_odom=(0.0, 0.0, 0.0))
    load(b2)
    assert "kitchen" not in b2.get_known_locations()


def test_without_a_room_nothing_changes(tmp_path):
    b1 = bridge(tmp_path, epoch=111, room=None, map_in_odom=(0.0, 0.0, 0.0))
    b1.add_known_location("door", 1.0, 0.0, 0.0)
    assert b1.get_known_locations()["door"] == (1.0, 0.0, 0.0)      # same session: as before
    b2 = bridge(tmp_path, epoch=222, room="r1", map_in_odom=(0.0, 0.0, 0.0))
    load(b2)
    assert "door" not in b2.get_known_locations()                   # unanchored: still dropped, safely


def test_old_four_field_files_still_load(tmp_path):
    (tmp_path / "locations.json").write_text(json.dumps({"sofa": [1, 2, 3, 111]}))
    b = bridge(tmp_path, epoch=111, room="r1", map_in_odom=(0.0, 0.0, 0.0))
    load(b)
    assert b.get_known_locations()["sofa"] == (1, 2, 3)
