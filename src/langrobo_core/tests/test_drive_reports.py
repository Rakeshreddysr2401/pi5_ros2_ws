"""Where the robot is, and how its drives ended -- told by the robot, not
guessed from the conversation.

2026-10-04, a Studio conversation: "where are you now?" got "1.5 m from the
door" after the robot had arrived (Studio threw the arrival away -- no
listener), "check your location again" got a STOP, and the "say good
morning" errand of a later drive could never run. No robot, no LLM.
"""

import time

import pytest

from langrobo_core.tools import NAVIGATE_TOOLS, CHAT_TOOLS, _bridge
from langrobo_core.tools import movement
from langrobo_core.tools.system import get_robot_status


class _Bridge:
    robot_body = "rover"

    def __init__(self, pose=(1.0, 2.0, 90.0), nav=None):
        self._pose, self._nav = pose, nav or {"active": False, "goal": None, "last": None}

    def get_current_pose(self):
        return self._pose

    def navigation_state(self):
        return self._nav

    def frame_age(self):
        return 0.5


@pytest.fixture
def bridge(monkeypatch):
    def use(**kw):
        b = _Bridge(**kw)
        monkeypatch.setattr(_bridge, "_instance", b)
        return b
    return use


# ── get_robot_status: where am I, am I still driving ────────────────────────

def test_status_says_where_the_robot_is(bridge):
    bridge()
    out = get_robot_status.invoke({})
    assert "x=1.00 m, y=2.00 m" in out and "haven't driven" in out


def test_status_while_driving_gives_the_distance_left(bridge):
    bridge(pose=(0.0, 0.0, 0.0), nav={"active": True, "last": None, "goal": {
        "x": 3.0, "y": 4.0, "label": "near the door", "since": time.time() - 60}})
    out = get_robot_status.invoke({})
    assert "still driving to 'near the door'" in out and "5.0 m to go" in out


def test_status_after_arrival_says_it_arrived(bridge):
    bridge(nav={"active": False, "goal": None, "last": {
        "success": True, "message": "I've arrived", "label": "near the door",
        "at": time.time() - 30}})
    out = get_robot_status.invoke({})
    assert "last drive to 'near the door' ARRIVED 30 s ago" in out


def test_status_after_a_failure_says_why(bridge):
    bridge(nav={"active": False, "goal": None, "last": {
        "success": False, "message": "no path", "label": "near the man",
        "at": time.time() - 300}})
    assert "FAILED 5 min ago: no path" in get_robot_status.invoke({})


def test_status_without_a_pose_says_so(bridge):
    bridge(pose=None)
    assert "don't know where I am" in get_robot_status.invoke({})


def test_navigate_can_answer_where_am_i():
    """The follow-up to a drive stays with navigate (sticky)."""
    assert get_robot_status in NAVIGATE_TOOLS and get_robot_status in CHAT_TOOLS


# ── nav_report: the one arrival report, for agent_node and Studio ───────────

def test_report_of_an_arrival_with_an_errand_and_a_target():
    movement.remember_requester({"channel": "voice"}, then="say good morning",
                                target="the man in the blue shirt")
    rep = movement.nav_report(True, "I've arrived at 'near the man'.")
    assert rep["text"].startswith("[SYSTEM] Navigation succeeded: I've arrived")
    assert "say good morning" in rep["text"] and "Do it now" in rep["text"]
    target, at = rep["check"]
    assert target == "the man in the blue shirt"
    assert rep["text"][:at].endswith("'near the man'.")      # the check goes after the head
    assert not rep["quiet"] and rep["sender"] is None


def test_report_of_a_failure_says_the_errand_was_not_done():
    movement.remember_requester({"channel": "voice"}, then="say good morning", target="dad")
    rep = movement.nav_report(False, "no path")
    assert "NOT done" in rep["text"] and rep["check"] is None


def test_report_of_a_telegram_drive_is_quiet_and_routed():
    movement.remember_requester({"channel": "telegram", "sender_name": "Mom",
                                 "sender_role": "family"})
    rep = movement.nav_report(True, "arrived")
    assert rep["quiet"] and rep["sender"] == ("Mom", "family")
    assert "send_telegram_message" in rep["text"]


def test_requester_outside_a_graph_has_no_thread():
    movement.remember_requester({"channel": "voice"})
    assert movement.get_last_nav_requester()["thread_id"] is None


def test_requester_inside_a_graph_run_remembers_its_thread():
    """Studio posts the report to the thread whose tool started the drive."""
    from typing import TypedDict

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    class S(TypedDict):
        x: int

    def node(state):
        movement.remember_requester({"channel": "voice"})
        return {"x": 1}

    g = StateGraph(S)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    g.compile(checkpointer=MemorySaver()).invoke(
        {"x": 0}, {"configurable": {"thread_id": "thread-42"}})
    assert movement.get_last_nav_requester()["thread_id"] == "thread-42"
