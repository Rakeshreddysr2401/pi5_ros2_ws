"""graph/build.py fresh-view-first: a current-view question gets a CURRENT photo.

2026-10-02 on the rover: the prompt said to look again when the last view was
over a minute old, every turn carried the view's age, and a second "what do
you see?" was still answered from a 73-second-old photo (once from before the
lights came on). The check runs BEFORE local_agent's LLM call, so nothing
stale is ever streamed to the speaker.
"""
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import langrobo_core.graph.build as gb
from langrobo_core.utils import pose_stamp

PHOTO = HumanMessage(content=[{"type": "text", "text": f"{pose_stamp.CAMERA_VIEW_MARKER} — taken at 01:00:00]"},
                              {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}])


@pytest.fixture
def run(monkeypatch):
    """Run local_agent (or another agent) through the real wrapper with a
    fake model; returns (what the model was shown, what the node returned)."""
    looks = []

    def fake_look(call_id):
        looks.append(call_id)
        return [ToolMessage("Captured the current camera view.", tool_call_id=call_id), PHOTO]
    monkeypatch.setattr(gb, "_take_look", fake_look)

    def go(user, agent="local_agent", view_age=None, history=()):
        if view_age is None:
            pose_stamp.forget_view()
        else:
            pose_stamp.record_view((0.0, 0.0, 0.0), when=time.time() - view_age)
        shown = []

        def node(state):
            shown.extend(state["messages"])
            return {"messages": [AIMessage(content="A box on the floor.")]}
        state = {"messages": list(history) + [HumanMessage(content=user)], "agent_run_counts": {}}
        out = gb._loop_guarded(agent, node)(state)
        return shown, out, looks
    yield go
    pose_stamp.forget_view()


def test_a_stale_view_is_replaced_before_the_model_answers(run):
    shown, out, looks = run("Mitra, what do you see?", view_age=73)
    assert len(looks) == 1
    assert shown[-1] is PHOTO, "the model sees the fresh photo"
    calls = [m for m in out["messages"] if isinstance(m, AIMessage) and m.tool_calls]
    assert calls[0].tool_calls[0]["name"] == "look"
    assert out["messages"][-1].content == "A box on the floor."


def test_no_photo_at_all_looks_first(run):
    _, _, looks = run("what are you looking at?")
    assert len(looks) == 1


def test_a_recent_view_is_used_as_it_is(run):
    _, out, looks = run("what do you see?", view_age=20)
    assert looks == [] and len(out["messages"]) == 1


def test_a_follow_up_about_the_scene_is_not_a_reason_to_look(run):
    _, _, looks = run("what colour is the box?", view_age=300)
    assert looks == []


def test_other_agents_are_left_alone(run):
    _, _, looks = run("what do you see?", agent="chat", view_age=300)
    assert looks == []


def test_a_turn_that_already_looked_does_not_look_twice(run):
    looked = [AIMessage(content="", tool_calls=[{"name": "look", "args": {}, "id": "l1", "type": "tool_call"}]),
              ToolMessage("Captured the current camera view.", tool_call_id="l1"), PHOTO]
    # the agent's second step of the same turn: its look() is after the user's message
    shown, _, looks = run("what do you see?", view_age=300)
    looks.clear()
    state = {"messages": [HumanMessage(content="what do you see?")] + looked, "agent_run_counts": {}}
    gb._loop_guarded("local_agent", lambda s: {"messages": [AIMessage(content="ok")]})(state)
    assert looks == []
