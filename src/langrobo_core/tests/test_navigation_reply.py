"""No tool-free action promises, including after a refused previous turn."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from langrobo_core.agents import factory
from langrobo_core.registry import SPECS
from test_blank_reply_retry import _FakeLLM


def run(monkeypatch, reply, messages):
    fake = _FakeLLM([reply])
    monkeypatch.setattr(factory, "get_llm", lambda *args, **kwargs:
                        fake.model_copy(update={"streaming": kwargs["streaming"]})
                        if "streaming" in kwargs else fake)
    node, _ = factory.build_agent(SPECS["navigate"])
    out = node({"messages": messages})
    return out["messages"][0], fake.calls


@pytest.mark.parametrize("history", [[], [
    HumanMessage("come little back"),
    ToolMessage("Movement refused; moved 0 cm", tool_call_id="old", name="move_robot"),
    AIMessage("Something is in the way."),
]])
def test_promise_without_a_tool_becomes_clarification_before_speech(monkeypatch, history):
    msg, calls = run(monkeypatch, AIMessage("I will try to move back again."),
                     history + [HumanMessage("No, there is nothing you can come back")])
    assert msg.content == factory._MOTION_CLARIFICATION
    assert calls == [False]  # unsupported text cannot stream ahead of validation


def test_routing_result_is_not_evidence_of_movement(monkeypatch):
    msg, _ = run(monkeypatch, AIMessage("I have moved."), [
        HumanMessage("go back"),
        ToolMessage("navigate", tool_call_id="h1", name="handover"),
    ])
    assert msg.content == factory._MOTION_CLARIFICATION


def test_actual_tool_call_is_preserved(monkeypatch):
    reply = AIMessage("", tool_calls=[{
        "name": "move_robot", "args": {"command": "B:10"}, "id": "1"}])
    msg, _ = run(monkeypatch, reply, [HumanMessage("back ten centimetres")])
    assert msg == reply


def test_after_a_tool_the_answer_still_streams(monkeypatch):
    msg, calls = run(monkeypatch, AIMessage("Moved back ten centimetres."), [
        HumanMessage("back ten centimetres"),
        ToolMessage("Movement done: B:10", tool_call_id="1", name="move_robot"),
    ])
    assert msg.content == "Moved back ten centimetres."
    assert calls == [True]


def test_room_talk_can_still_be_silent(monkeypatch):
    msg, _ = run(monkeypatch, AIMessage("[silent]"), [HumanMessage("Me")])
    assert msg.content == "[silent]"


def test_offline_message_is_not_replaced_with_a_direction_question(monkeypatch):
    msg, _ = run(monkeypatch, AIMessage(factory._OFFLINE_MESSAGE), [HumanMessage("go back")])
    assert msg.content == factory._OFFLINE_MESSAGE


def test_streaming_backup_cannot_bypass_validation(monkeypatch):
    from langrobo_core.services import llm as service
    backup = _FakeLLM([AIMessage("I will move back again.")])
    monkeypatch.setattr(service, "primary_available", lambda: False)
    monkeypatch.setattr(service, "get_fallback_llm", lambda: backup)
    msg, _ = run(monkeypatch, AIMessage("unused"), [HumanMessage("go back")])
    assert msg.content == factory._MOTION_CLARIFICATION
    assert backup.calls == [False]


def test_first_navigation_call_prevents_the_thinking_loop(monkeypatch):
    asked = []
    fake = _FakeLLM([AIMessage("<channel|>Could you repeat the complete request?")])

    def client(*args, **kwargs):
        asked.append(kwargs)
        return fake.model_copy(update={"streaming": kwargs.get("streaming", True)})

    monkeypatch.setattr(factory, "get_llm", client)
    node, _ = factory.build_agent(SPECS["navigate"])
    out = node({"messages": [HumanMessage("Started")]})
    assert {"streaming": False, "ban_thinking": True} in asked
    assert fake.calls == [False]
    assert out["messages"][0].content == factory._MOTION_CLARIFICATION
