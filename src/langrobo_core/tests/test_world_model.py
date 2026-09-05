"""services/world_model.py + tools/world.py — the robot's spatial memory.

Each test here pins a behaviour that was missing before the WorldModel existed:
positions died with the process, one label held one position, and nothing could
put the map into words.
"""

import json

import pytest

from langrobo_core.services.world_model import WorldModel, _humanise_age


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def wm(tmp_path=None, clock=None, **kw):
    return WorldModel(path=str(tmp_path / "world.json") if tmp_path else None,
                      clock=clock or FakeClock(), save_interval_s=0.0, **kw)


# ── Merging vs distinct instances ────────────────────────────────────────────

def test_repeat_sighting_of_one_object_merges():
    m = wm()
    m.observe("chair", 1.0, 1.0)
    m.observe("chair", 1.1, 1.05)          # depth noise, same chair
    assert m.summary("chair")["count"] == 1


def test_two_chairs_are_two_instances():
    m = wm()
    m.observe("chair", 0.0, 0.0)
    m.observe("chair", 4.0, 0.0)
    assert m.summary("chair")["count"] == 2


def test_nearest_beats_freshest():
    """The bug this replaces: the LAST-published chair won, so the robot drove
    past the chair beside it to one across the room."""
    m = wm()
    m.observe("chair", 5.0, 0.0)           # far, seen first
    m.observe("chair", 1.0, 0.0)           # near
    m.observe("chair", 5.0, 0.0)           # far again — now the freshest
    assert m.freshest("chair")["x"] == 5.0
    assert m.nearest("chair", 0.0, 0.0)["x"] == 1.0


def test_nearest_reports_distance():
    m = wm()
    m.observe("bottle", 3.0, 4.0)
    assert m.nearest("bottle", 0.0, 0.0)["distance_m"] == pytest.approx(5.0)


def test_instances_are_capped_evicting_the_stalest():
    clock = FakeClock()
    m = wm(clock=clock, max_instances=2)
    for x in (0.0, 5.0, 10.0):
        m.observe("chair", x, 0.0)
        clock.advance(10)
    xs = {m.nearest("chair", x, 0.0)["x"] for x in (0.0, 5.0, 10.0)}
    assert 0.0 not in xs and m.summary("chair")["count"] == 2


# ── Ages and freshness ───────────────────────────────────────────────────────

def test_age_filters_and_never_goes_negative():
    clock = FakeClock()
    m = wm(clock=clock)
    m.observe("person", 1.0, 0.0)
    clock.advance(10)
    assert m.freshest("person", max_age_s=3.0) is None
    assert m.freshest("person")["age_s"] == pytest.approx(10.0)
    clock.t -= 60                      # a clock step backwards must not wrap
    assert m.freshest("person")["age_s"] >= 0.0


def test_age_phrases_are_speakable():
    for seconds, expected in ((5, "just now"), (300, "about 5 minutes ago"),
                              (7200, "about 2 hours ago"), (200000, "about 2 days ago")):
        assert _humanise_age(seconds) == expected


def test_all_fresh_only_returns_recent_labels():
    clock = FakeClock()
    m = wm(clock=clock)
    m.observe("chair", 1.0, 0.0)
    clock.advance(100)
    m.observe("tv", 2.0, 0.0)
    assert set(m.all_fresh(max_age_s=10.0)) == {"tv"}
    assert set(m.labels()) == {"chair", "tv"}


# ── Persistence: the actual gap this closes ──────────────────────────────────

def test_positions_survive_a_restart(tmp_path):
    clock = FakeClock()
    first = wm(tmp_path, clock)
    first.observe("chair", 2.0, 1.0)
    first.save()

    # "systemctl restart langrobo-brain" — before this, the chair was forgotten
    # while saved LOCATIONS survived, which is the inconsistency being fixed.
    second = WorldModel(path=str(tmp_path / "world.json"), clock=clock)
    assert second.freshest("chair")["x"] == 2.0


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    m = wm(tmp_path)
    m.observe("tv", 1.0, 1.0)
    m.save()
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"world.json"}
    assert json.loads((tmp_path / "world.json").read_text())["version"] == 1


def test_a_corrupt_map_file_does_not_stop_the_robot(tmp_path):
    (tmp_path / "world.json").write_text("{ this is not json")
    m = WorldModel(path=str(tmp_path / "world.json"), clock=FakeClock())
    assert m.labels() == []            # degraded, not crashed (CLAUDE.md #4)


def test_garbage_instances_are_skipped_on_load(tmp_path):
    (tmp_path / "world.json").write_text(json.dumps({
        "version": 1,
        "objects": {"chair": [{"x": 1.0, "y": 2.0, "at": 5.0},
                              {"x": "not-a-number", "y": 0.0, "at": 5.0}]},
    }))
    m = WorldModel(path=str(tmp_path / "world.json"), clock=FakeClock())
    assert m.summary("chair")["count"] == 1


def test_forget_removes_a_moved_object(tmp_path):
    m = wm(tmp_path)
    m.observe("backpack", 1.0, 1.0)
    assert m.forget("backpack") == 1
    assert m.freshest("backpack") is None
    assert m.forget("backpack") == 0
