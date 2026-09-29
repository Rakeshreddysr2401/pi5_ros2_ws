"""CAP_MOVE is enforced: only roles with "move" may drive the robot.

It was defined in services/permissions.py and checked nowhere -- any
allowlisted Telegram member (family, guest) could send the robot around the
house. Stopping stays open to everyone.
"""

import pytest
from langchain_core.messages import AIMessage

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge
import langrobo_core.tools.movement as mv
from langrobo_core.tools.approach import approach_described_object, scan_surroundings
from langrobo_core.tools.movement import move_robot, navigate_to_pose, save_location

from test_movement import fake_twist  # noqa: F401 -- fixture


def _state(role, name="Mom"):
    return {"channel": "telegram", "sender_name": name, "sender_role": role, "messages": []}


@pytest.fixture(autouse=True)
def _bridge_and_auto(monkeypatch):
    _bridge._instance = None
    _bridge.init(StubBridge())
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: False)
    yield


@pytest.mark.parametrize("role", ["family", "guest"])
def test_family_and_guests_cannot_drive(role, fake_twist):
    moved = []
    mv_exact, mv.exact_turn = mv.exact_turn, lambda b, d: moved.append(d)
    try:
        out = move_robot.invoke({"command": "L:90", "state": _state(role)})
    finally:
        mv.exact_turn = mv_exact
    assert "not allowed to move" in out and moved == []
    assert "not allowed" in navigate_to_pose.invoke({"location": "kitchen", "state": _state(role)})
    assert "not allowed" in approach_described_object.invoke(
        {"description": "the bottle", "state": _state(role)})
    assert "not allowed" in scan_surroundings.invoke({"state": _state(role)})
    assert "not allowed" in save_location.invoke({"name": "desk", "state": _state(role)})


def test_anyone_can_stop(fake_twist):
    out = move_robot.invoke({"command": "S", "state": _state("guest")})
    assert "not allowed" not in out


def test_owner_and_voice_can_drive(fake_twist, monkeypatch):
    monkeypatch.setattr(mv, "exact_turn", lambda b, d: {"ok": True, "result": "reached",
                                                        "turned_deg": d, "moved_cm": 0.0})
    assert "not allowed" not in move_robot.invoke({"command": "L:90", "state": _state("owner")})
    assert "not allowed" not in move_robot.invoke({"command": "L:90"})      # voice / Studio: no role


def test_the_graph_injects_the_senders_role(fake_twist, monkeypatch):
    """Through the real graph: a family member's "go forward" reaches
    move_robot with their role, and is refused there."""
    from langchain_core.messages import HumanMessage, ToolMessage
    from langrobo_core.agents import factory
    from langrobo_core.graph import build_graph
    moved = []
    monkeypatch.setattr(mv, "exact_drive", lambda b, m: moved.append(m))

    class Nav:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, *a, **k):
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content=messages[-1].content)
            return AIMessage(content="", tool_calls=[{"name": "move_robot", "id": "1",
                                                      "args": {"command": "F:20"}}])

    monkeypatch.setattr(factory, "get_llm", lambda name, **kw: Nav())
    out = build_graph().invoke({"messages": [HumanMessage(content="go forward 20 cm")],
                                "active_agent": "navigate", **{k: v for k, v in _state("family").items()
                                                               if k != "messages"}})
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert "not allowed to move" in tool_msgs[0].content and moved == []
