"""Object memory across power-off, through the house map (owner, 2026-11-16).

Odom restarts at 0 every boot; the saved house map does not. An object seen
before a power cycle must come back at the same spot in the room -- in the NEW
odom -- once map -> odom is known; until then it waits (never deleted); in
another room's map it stays aside; with no house map it dies with its origin.
"""
import json
import math

import pytest

from langrobo_core.services import object_memory as om

OLD, NEW = 1000.0, 2000.0


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_OBJECT_MEMORY", str(tmp_path / "om.json"))
    om.set_home(None)
    yield
    om.set_home(None)


def home(room, x, y, yaw):
    return lambda: {"room": room, "map_in_odom": (x, y, yaw)}


def test_seen_before_a_power_cycle_comes_back_at_the_same_spot():
    # boot 1: the map frame sits at (1, 2), turned 90 deg, in odom
    om.set_home(home("house1", 1.0, 2.0, 90.0))
    e = om.remember("red sofa", 3.0, 4.0, (0, 0, 0), OLD)
    mx, my = e["home"]["map"]                      # the sofa in the map: (2, -2)
    assert (round(mx, 3), round(my, 3)) == (2.0, -2.0)
    # boot 2: odom restarted; the same map is now at (-1, 0), not turned
    om.set_home(home("house1", -1.0, 0.0, 0.0))
    hits = om.recall("sofa", NEW)
    assert len(hits) == 1 and hits[0]["via_home"]
    assert math.hypot(hits[0]["x"] - 1.0, hits[0]["y"] - (-2.0)) < 1e-6   # map (2,-2) -> odom (1,-2)


def test_it_waits_while_map_to_odom_is_not_known_and_is_never_deleted():
    om.set_home(home("house1", 0.0, 0.0, 0.0))
    om.remember("television", 2.0, 0.0, None, OLD)
    om.set_home(None)                              # boot 2, relocalisation not done yet
    assert om.recall("television", NEW) == []
    om.remember("blue bottle", 0.5, 0.5, None, NEW)   # a write meanwhile must not lose it
    om.set_home(home("house1", 0.0, 0.0, 0.0))
    names = {e["description"] for e in om.recall("", NEW)}
    assert names == {"television", "blue bottle"}


def test_another_rooms_map_keeps_it_aside():
    om.set_home(home("house1", 0.0, 0.0, 0.0))
    om.remember("bed", 1.0, 1.0, None, OLD)
    om.set_home(home("office", 0.0, 0.0, 0.0))
    assert om.recall("bed", NEW) == []
    om.set_home(home("house1", 0.0, 0.0, 0.0))
    assert len(om.recall("bed", NEW)) == 1


def test_without_a_house_map_it_dies_with_its_origin_as_before():
    om.remember("chair", 1.0, 1.0, None, OLD)       # no home known
    assert om.recall("chair", NEW) == []
    om.remember("cup", 0.2, 0.2, None, NEW)          # the next write
    with open(om.path()) as f:
        data = json.load(f)
    assert [e["description"] for e in data["objects"]] == ["cup"] and data["aside"] == []


def test_a_new_sighting_updates_the_old_one_not_a_second_copy():
    om.set_home(home("house1", 0.0, 0.0, 0.0))
    om.remember("dining table", 2.0, 2.0, None, OLD)
    om.remember("dining table", 2.1, 2.0, None, NEW)     # seen again after the restart
    hits = om.recall("dining table", NEW)
    assert len(hits) == 1 and hits[0]["sightings"] == 2 and not hits[0].get("via_home")


def test_old_sightings_last_days_not_an_hour():
    om.remember("bed", 1.0, 1.0, None, NEW, when=1000.0)
    assert om.recall("bed", NEW, now=1000.0 + 3 * 86400)
    assert not om.recall("bed", NEW, now=1000.0 + 30 * 86400)


def test_helpers_for_the_lookup_by_meaning():
    om.remember("white television", 1.0, 0.0, None, NEW)
    om.remember("white television", 3.0, 0.0, None, NEW)
    om.remember("wooden dining table", 0.0, 2.0, None, NEW)
    lab = om.labels(NEW)
    assert set(lab) == {"white television", "wooden dining table"} and len(lab["white television"]) == 2
    assert om.colours_clash("red bottle", "orange bottle") and not om.colours_clash("tv", "white television")
    assert om.object_part("the white box on the floor") == "white box"
