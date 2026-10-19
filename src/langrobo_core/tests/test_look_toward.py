"""look_toward: turn to where a thing was seen, then a fresh photo (real robot 2026-10-18:
"how many books are on the shelf?" with the shelf behind was answered from an old photo)."""
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

import langrobo_core.tools._bridge as _bridge
import importlib
lt = importlib.import_module("langrobo_core.tools.look_toward")   # the module: the package re-exports the TOOL under this name
import langrobo_core.tools.movement as mv
from langrobo_core.bridges.stub import StubBridge
from langrobo_core.tools import LOCAL_AGENT_TOOLS, MOTION_TOOL_NAMES

from test_movement import fake_twist  # noqa: F401 -- fixture: geometry_msgs without ROS

pytestmark = pytest.mark.usefixtures("fake_twist")
_bridge._instance = None
_bridge.init(StubBridge())


def _call(desc, monkeypatch, bearing, source="memory", turn_ok=True):
    turns = []
    monkeypatch.setattr(mv, "teleop_is_manual", lambda: False)
    monkeypatch.setattr(lt, "where", lambda d: (bearing, source))
    monkeypatch.setattr(lt._mv, "turn_robot", lambda b, deg: (turns.append(deg) or (turn_ok, "" if turn_ok else "wall")))
    out = lt.look_toward.invoke({"type": "tool_call", "id": "c1", "name": "look_toward",
                                 "args": {"description": desc}})
    return turns, out.update["messages"]


def test_turns_to_it_then_looks(monkeypatch):
    turns, msgs = _call("the shelf", monkeypatch, 170.0)
    assert turns == [170.0]
    assert isinstance(msgs[0], ToolMessage) and "Turned 170 degrees left to face the shelf" in msgs[0].content
    assert any(isinstance(m, HumanMessage) for m in msgs) or "camera" in msgs[0].content.lower()


def test_already_facing_it_does_not_turn(monkeypatch):
    turns, _ = _call("the door", monkeypatch, 12.0)
    assert turns == []


def test_unknown_thing_says_so_without_moving(monkeypatch):
    turns, msgs = _call("the cat", monkeypatch, None, "unknown")
    assert turns == [] and "don't know where the cat is" in msgs[0].content


def test_blocked_turn_is_reported(monkeypatch):
    turns, msgs = _call("the shelf", monkeypatch, -120.0, turn_ok=False)
    assert turns == [-120.0] and "could not turn" in msgs[0].content


def test_plain_directions():
    assert lt.where("behind you") == (180.0, "direction")
    assert lt.where("to your left") == (90.0, "direction")
    assert lt.where("right") == (-90.0, "direction")


def test_wired_as_a_motion_tool_of_local_agent():
    assert "look_toward" in {t.name for t in LOCAL_AGENT_TOOLS}
    assert "look_toward" in MOTION_TOOL_NAMES
