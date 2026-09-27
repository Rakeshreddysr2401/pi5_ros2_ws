"""An empty streamed reply is retried once unstreamed (agents/factory.py).

Gemma 4 on the Mac's llama.cpp sometimes wraps its answer in channel markers;
streamed, the server files the whole answer as hidden reasoning and the turn
ends with no text -- a Telegram user got "Sorry, I couldn't come up with a
reply" (2026-09-27). Unstreamed, the same answer arrives with its markers,
which strip_thought_residue removes.
"""
from langchain_core.messages import AIMessage, HumanMessage

import langrobo_core.agents.factory as factory
from langrobo_core.registry import SPECS


class _FakeLLM:
    def __init__(self, replies, streaming=True):
        self.replies, self.streaming, self.calls = replies, streaming, []

    def model_copy(self, update):
        clone = _FakeLLM(self.replies, update.get("streaming", self.streaming))
        clone.calls = self.calls
        return clone

    def bind_tools(self, tools):
        return self

    def invoke(self, msgs):
        self.calls.append(self.streaming)
        return self.replies[len(self.calls) - 1]


def _run(monkeypatch, replies):
    fake = _FakeLLM(replies)
    monkeypatch.setattr(factory, "get_llm", lambda name: fake)
    node, _ = factory.build_agent(SPECS["navigate"])
    out = node({"messages": [HumanMessage(content="go near the white chair")]})
    return out["messages"][0], fake.calls


def test_blank_streamed_reply_is_retried_unstreamed(monkeypatch):
    msg, calls = _run(monkeypatch, [
        AIMessage(content=""),
        AIMessage(content="<channel|>thought\n<channel|>I could not find the white chair."),
    ])
    assert calls == [True, False]
    assert msg.content == "I could not find the white chair."


def test_a_thought_only_reply_counts_as_blank(monkeypatch):
    msg, calls = _run(monkeypatch, [AIMessage(content="thought"), AIMessage(content="Done.")])
    assert calls == [True, False] and msg.content == "Done."


def test_normal_and_tool_call_replies_are_not_retried(monkeypatch):
    _, calls = _run(monkeypatch, [AIMessage(content="On my way.")])
    assert calls == [True]
    tc = AIMessage(content="", tool_calls=[{"name": "move_robot", "args": {"command": "S"}, "id": "1"}])
    _, calls = _run(monkeypatch, [tc])
    assert calls == [True]
