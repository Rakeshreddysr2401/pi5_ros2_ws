"""One whole turn through AgentNode._process, with a fake graph, speaker and Telegram.

Pins what a turn does with its reply and its state (history, sticky agent): spoken on a
voice turn, sent on a Telegram turn, nothing kept on barge-in or an empty reply. Written
2026-11-24 before _process was split into steps, and run on both versions.
"""
import threading
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from langrobo_core import routing as intent_routing
from langrobo_core.services import jev
from langrobo_ros import agent_node as AN
from langrobo_ros.agent_node import AgentNode


class Rec:
    """Records every call made on it: rec.calls == [(name, args), ...]."""
    def __init__(self, **returns):
        self.calls, self._returns = [], returns

    def __getattr__(self, name):
        def call(*a, **k):
            self.calls.append((name, a))
            return self._returns.get(name)
        return call

    def names(self):
        return [c[0] for c in self.calls]


class FakeGraph:
    """stream() yields the state with `reply` appended; `on_step` runs before the reply."""
    def __init__(self, reply, agent="chat", on_step=None):
        self.reply, self.agent, self.on_step = reply, agent, on_step
        self.inputs = []

    def stream(self, state, config=None, stream_mode=None):
        self.inputs.append(state)
        yield {"messages": state["messages"], "active_agent": state["active_agent"]}
        if self.on_step:
            self.on_step()
        yield {"messages": state["messages"] + [AIMessage(self.reply)], "active_agent": self.agent}


def make_node(graph):
    node = AgentNode.__new__(AgentNode)
    log = SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)
    node.get_logger = lambda: log
    node._queue_lock, node._history_lock = threading.Lock(), threading.Lock()
    node._turn_interrupt = threading.Event()
    node._turn_channel, node._turn_active, node._turn_moving = "voice", False, False
    node._pub_thinking = Rec()
    node._bridge = Rec()
    node._telegram = Rec()
    node._history = [HumanMessage("earlier"), AIMessage("earlier reply")]
    node._sticky_agent = None
    node._stream_speech = False
    node._timing_handler = Rec()
    node._graph = graph
    node._max_history = 50
    node._last_spoke_ts = 0.0
    node._start_cache_warm = lambda: None
    node._trace_metadata = lambda *a, **k: {}
    return node


@pytest.fixture(autouse=True)
def routing_off(monkeypatch):
    monkeypatch.setattr(jev, "mode", lambda: "off")
    monkeypatch.setattr(intent_routing, "mode", lambda: "off")
    monkeypatch.setattr(AN.addressing, "addressed", lambda *a: True)


def test_voice_turn_is_spoken_and_kept():
    g = FakeGraph("Hello there.", agent="local_agent")
    node = make_node(g)
    node._process("kino hello")
    assert ("publish_speech", ("Hello there.",)) in node._bridge.calls
    assert node._telegram.calls == []
    assert node._sticky_agent == "local_agent"
    assert [m.content for m in node._history][-1] == "Hello there."
    assert len(node._history) == 4
    assert g.inputs[0]["active_agent"] == "chat" and g.inputs[0]["channel"] == "voice"
    assert node._turn_active is False
    assert [c[1][0].data for c in node._pub_thinking.calls] == [True, False]


def test_telegram_turn_answers_the_chat_not_the_speaker():
    g = FakeGraph("It is sunny.")
    node = make_node(g)
    tg = SimpleNamespace(name="Mom", role="family", chat_id=7, photo=None)
    node._process("weather?", telegram=tg)
    assert ("send_message", (7, "It is sunny.")) in node._telegram.calls
    assert "publish_speech" not in node._bridge.names()
    assert node._pub_thinking.calls == []
    assert g.inputs[0]["sender_name"] == "Mom"
    assert "[Telegram from Mom]" in g.inputs[0]["messages"][-1].content


def test_barge_in_keeps_nothing():
    node = make_node(None)
    node._graph = FakeGraph("Long answer.", on_step=node._turn_interrupt.set)
    before = list(node._history)
    node._process("kino tell me a story")
    assert node._history == before and node._sticky_agent is None
    assert "publish_speech" not in node._bridge.names()


def test_empty_reply_keeps_nothing_and_a_texter_gets_an_apology():
    node = make_node(FakeGraph(""))
    before = list(node._history)
    tg = SimpleNamespace(name="Mom", role="family", chat_id=7, photo=None)
    node._process("hm", telegram=tg)
    assert node._history == before and node._sticky_agent is None
    assert ("send_message", (7, "Sorry, I couldn't come up with a reply.")) in node._telegram.calls


def test_a_graph_error_is_apologised_for():
    class Broken:
        def stream(self, *a, **k):
            raise RuntimeError("server down")
            yield
    node = make_node(Broken())
    node._process("kino hello")
    assert node._bridge.calls[-1][0] == "publish_speech"
    assert "trouble" in node._bridge.calls[-1][1][0]
    assert node._turn_active is False
