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


asked = []      # the kwargs every get_llm call was made with


def _run(monkeypatch, replies):
    fake = _FakeLLM(replies)
    asked.clear()
    monkeypatch.setattr(factory, "get_llm", lambda name, **kw: asked.append(kw) or fake)
    node, _ = factory.build_agent(SPECS["navigate"])
    out = node({"messages": [HumanMessage(content="go near the white chair")]})
    return out["messages"][0], fake.calls


def test_the_retry_bans_the_thinking_token(monkeypatch):
    """Normal calls carry no ban (services/llm.py); the retry after a blank
    reply does, so a thinking loop cannot happen twice."""
    _run(monkeypatch, [AIMessage(content=""), AIMessage(content="On my way.")])
    assert {"ban_thinking": True} in asked


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


def test_still_blank_after_a_tool_answers_with_the_tools_words(monkeypatch):
    """Seen 3x on 2026-09-27: "On my way" never reached the Telegram user."""
    from langchain_core.messages import ToolMessage
    fake = _FakeLLM([AIMessage(content=""), AIMessage(content="")])
    monkeypatch.setattr(factory, "get_llm", lambda name, **kw: asked.append(kw) or fake)
    node, _ = factory.build_agent(SPECS["navigate"])
    tool = ToolMessage(content="I can see the white box — about 1.1 m away. On my way; "
                               "I'll say when I'm there. The robot has MOVED, so the "
                               "camera view has changed.", tool_call_id="1")
    out = node({"messages": [HumanMessage(content="go to the white box"),
                             AIMessage(content="", tool_calls=[{"name": "approach_described_object",
                                                               "args": {}, "id": "1"}]), tool]})
    assert out["messages"][0].content == ("I can see the white box — about 1.1 m away. "
                                          "On my way; I'll say when I'm there.")
