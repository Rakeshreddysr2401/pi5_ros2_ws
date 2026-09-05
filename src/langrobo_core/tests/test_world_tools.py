"""tools/world.py — the spatial memory, in words the speaker can use."""

from langrobo_core.bridges import StubBridge
from langrobo_core.services import world_model
from langrobo_core.services.world_model import WorldModel
from langrobo_core.tools import _bridge, CHAT_TOOLS, LOCAL_AGENT_TOOLS
from langrobo_core.tools.world import (forget_object, list_known_objects,
                                       where_is, _clock_direction)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def world_with(sightings, pose=(0.0, 0.0, 0.0)):
    """sightings: [(label, x, y, seconds_ago)] — aged by moving the clock, the
    way the production path ages them."""
    _bridge._instance = None
    b = StubBridge()
    b.pose = pose
    _bridge.init(b)
    clock = Clock()
    w = world_model.reset(WorldModel(path=None, clock=clock))
    newest = max((age for *_, age in sightings), default=0.0)
    for label, x, y, age in sorted(sightings, key=lambda s: -s[3]):
        clock.t = 1000.0 + (newest - age)
        w.observe(label, x, y, 0.0, 0.8)
    clock.t = 1000.0 + newest
    return w


def call(tool, **kw):
    return tool.invoke(kw)


# ── Registration: the capability has to reach the agents that need it ───────

def test_chat_and_local_agent_can_answer_where_questions():
    for tools in (CHAT_TOOLS, LOCAL_AGENT_TOOLS):
        names = {t.name for t in tools}
        assert {"where_is", "list_known_objects"} <= names


def test_local_agent_can_aim_the_camera():
    """It was the only agent that can SEE and the only one that could not AIM,
    so "look to your left" had no single owner."""
    assert "point_camera" in {t.name for t in LOCAL_AGENT_TOOLS}


# ── Phrasing (this text goes straight to TTS) ───────────────────────────────

def test_direction_is_said_not_measured():
    assert _clock_direction(0) == "straight ahead"
    assert _clock_direction(90) == "to my left"
    assert _clock_direction(-90) == "to my right"
    assert _clock_direction(180) == "behind me"


def test_where_is_takes_no_injected_state():
    """It never read the turn state, and requiring it barred the tool from the
    no-LLM fastpath lane, which calls tools outside any graph run."""
    assert "state" not in where_is.args


def test_where_is_reports_distance_and_direction():
    world_with([("chair", 3.0, 0.0, 0.0)])
    out = call(where_is, object_name="chair")
    assert "3.0 metres" in out and "straight ahead" in out
    assert "can see it" in out


def test_where_is_admits_it_is_remembering():
    world_with([("bag", 0.0, 2.0, 3600.0)])
    out = call(where_is, object_name="bag")
    assert "last saw it" in out and "about an hour ago" in out


def test_where_is_offers_what_it_does_know():
    world_with([("tv", 1.0, 0.0, 1.0)])
    out = call(where_is, object_name="unicorn")
    assert "never seen" in out and "tv" in out


def test_where_is_with_no_map_at_all_suggests_a_scan():
    world_with([])
    assert "scan" in call(where_is, object_name="chair").lower()


def test_where_is_without_localisation_stays_honest():
    """A real position the robot cannot relate to "here" must not become a
    confident direction."""
    world_with([("chair", 3.0, 0.0, 1.0)], pose=None)
    out = call(where_is, object_name="chair")
    assert "can't work out where I am" in out


def test_list_known_objects_separates_seen_now_from_remembered():
    world_with([("chair", 1.0, 0.0, 0.5), ("bag", 5.0, 0.0, 900.0)])
    out = call(list_known_objects)
    assert "can see chair" in out and "remember" in out and "bag" in out


def test_forget_object_clears_a_moved_thing():
    w = world_with([("bag", 1.0, 0.0, 60.0)])
    assert "Forgotten" in call(forget_object, object_name="bag")
    assert w.labels() == []
    assert "didn't have" in call(forget_object, object_name="bag")
