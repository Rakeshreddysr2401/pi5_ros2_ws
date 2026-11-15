"""survey.AutoLook / auto_look_tick: the robot takes survey photos by itself as the view
changes (owner, 2026-11-16: know the surroundings, not only what was asked about)."""
import pytest

from langrobo_core.tools import survey as sv


def test_the_rule():
    a = sv.AutoLook()
    assert a.due((0, 0, 0), 100.0, idle=True, queued=0), "the first view"
    a.taken((0, 0, 0), 100.0)
    assert not a.due((2, 0, 0), 104.0, True, 0), "at most one per MIN_GAP_S"
    assert not a.due((0.3, 0, 10), 120.0, True, 0), "same view: not moved / turned enough"
    assert a.due((0.9, 0, 0), 120.0, True, 0), "moved 0.9 m"
    assert a.due((0, 0, 45), 120.0, True, 0), "turned 45 deg"
    assert a.due((0, 0, -179), 120.0, True, 0) and not a.due((0, 0, 0), 120.0, True, 0)
    assert not a.due((5, 0, 0), 120.0, idle=False, queued=0), "a turn is running"
    assert not a.due((5, 0, 0), 120.0, True, queued=sv.MAX_QUEUE // 2), "the survey is behind"
    assert not a.due(None, 120.0, True, 0), "no pose"


class Bridge:
    def __init__(self, stamp=(5, 6)):
        self.pose, self.stamp, self.held = (0.0, 0.0, 0.0), stamp, []

    def get_current_pose(self):
        return self.pose

    def get_frame_stamped(self, max_age_s=None):
        return b"jpeg", self.stamp

    def hold_frame(self, stamp):
        self.held.append(stamp)

    def get_origin_epoch(self):
        return 7.0


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(sv, "_auto", sv.AutoLook())
    monkeypatch.setattr(sv, "ENABLED", True)
    monkeypatch.setattr(sv, "AUTO_LOOK", True)
    monkeypatch.setattr(sv, "VISION_TOOL_SLOT", 3)
    monkeypatch.setattr(sv, "_paused", 0)
    monkeypatch.setattr(sv, "_busy_probe", lambda: False)
    sent = []
    monkeypatch.setattr(sv, "submit", lambda *a, **k: sent.append((a, k)) or True)
    return sent


def test_a_tick_takes_holds_and_submits_once_per_view(fresh):
    b = Bridge()
    assert sv.auto_look_tick(b, idle=True, now=100.0)
    assert b.held == [(5, 6)] and fresh[0][0][3] == "auto" and fresh[0][1]["epoch"] == 7.0
    assert not sv.auto_look_tick(b, idle=True, now=110.0), "same view"
    b.pose = (1.0, 0.0, 0.0)
    assert sv.auto_look_tick(b, idle=True, now=120.0) and len(fresh) == 2


def test_no_camera_stamp_no_photo(fresh):
    b = Bridge(stamp=None)
    assert not sv.auto_look_tick(b, idle=True, now=100.0) and fresh == [] and b.held == []


def test_off_switches(fresh, monkeypatch):
    monkeypatch.setattr(sv, "AUTO_LOOK", False)
    assert not sv.auto_look_tick(Bridge(), idle=True, now=100.0)
    monkeypatch.setattr(sv, "AUTO_LOOK", True)
    monkeypatch.setattr(sv, "VISION_TOOL_SLOT", None)
    assert not sv.auto_look_tick(Bridge(), idle=True, now=100.0)


def test_an_approach_search_pauses_it(fresh):
    with sv.paused():
        assert not sv.auto_look_tick(Bridge(), idle=True, now=100.0)
