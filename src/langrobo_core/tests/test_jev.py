"""Jev (services/jev.py): the brain's quick decisions on TypeSafe's model.

No network: a fake server (httpx.MockTransport) answers in the API's shape
(simonw/llm-typesafe's reference client). What must hold:
  * off without a key, whatever LANGROBO_JEV says (rule 5);
  * one request per turn carries both questions, with the registry's own
    routing copy as the choice criteria;
  * every failure -- HTTP error, timeout, garbage -- is None, never a raise;
  * only a CONFIDENT pick changes the entry agent;
  * a near-sure vision read backs up the vision-question check.
"""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from langrobo_core.registry import AGENTS
from langrobo_core.services import jev


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("LANGROBO_JEV", "on")
    for k in jev.stats:
        monkeypatch.setitem(jev.stats, k, 0)
    monkeypatch.setattr(jev, "_client", None)
    yield


def _server(monkeypatch, handler):
    """Install a fake Jev; returns the list of request bodies it received."""
    seen = []

    def wrap(request):
        seen.append({"body": json.loads(request.content),
                     "auth": request.headers.get("Authorization"),
                     "url": str(request.url)})
        return handler(request)

    monkeypatch.setattr(jev, "_client", httpx.Client(transport=httpx.MockTransport(wrap)))
    return seen


def _answers(route="navigate", conf=0.97, vision=0.02):
    return {"answers": {
        "route": {"type": "choice", "choice": route, "confidence": conf,
                  "probabilities": {route: conf}},
        "vision": {"type": "noul", "noul": vision}},
        "model": "jev-latest", "usage": {"input_tokens": 90, "output_tokens": 0}}


# ── modes ───────────────────────────────────────────────────────────────────

def test_no_key_is_off_whatever_the_mode_says(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert jev.mode() == "off"
    assert jev.read_turn("go to the kitchen", AGENTS) is None


@pytest.mark.parametrize("value, expected", [
    ("on", "on"), ("shadow", "shadow"), ("off", "off"), ("", "off"), ("yes", "off")])
def test_modes(monkeypatch, value, expected):
    monkeypatch.setenv("LANGROBO_JEV", value)
    assert jev.mode() == expected


# ── the request ─────────────────────────────────────────────────────────────

def test_one_request_asks_route_and_vision_with_the_registrys_routing_copy(monkeypatch):
    seen = _server(monkeypatch, lambda r: httpx.Response(200, json=_answers()))
    read = jev.read_turn("go to the kitchen", AGENTS, "chat", "Hello!")
    assert len(seen) == 1
    body = seen[0]["body"]
    assert seen[0]["auth"] == "Bearer test-key" and seen[0]["url"] == jev.API_URL
    assert body["model"] == jev.MODEL
    assert body["state"] == {"utterance": "go to the kitchen",
                             "previous_assistant": "chat", "previous_reply": "Hello!"}
    route = body["questions"]["route"]
    assert route["type"] == "choice"
    assert route["criteria"] == {n: m["description"] for n, m in AGENTS.items()}
    assert body["questions"]["vision"]["type"] == "noul"
    assert read.route == "navigate" and read.route_conf == 0.97 and read.vision == 0.02


def test_previous_reply_is_capped(monkeypatch):
    seen = _server(monkeypatch, lambda r: httpx.Response(200, json=_answers()))
    jev.read_turn("and now?", AGENTS, "chat", "x" * 1000)
    assert len(seen[0]["body"]["state"]["previous_reply"]) == 300


# ── failures never raise ────────────────────────────────────────────────────

def test_http_error_is_none_and_counted(monkeypatch):
    _server(monkeypatch, lambda r: httpx.Response(401, text="bad key"))
    assert jev.read_turn("hello", AGENTS) is None
    assert jev.stats["errors"] == 1


def test_timeout_is_none_and_counted(monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    _server(monkeypatch, slow)
    assert jev.read_turn("hello", AGENTS) is None
    assert jev.stats["timeouts"] == 1 and jev.stats["errors"] == 0


def test_garbage_is_none(monkeypatch):
    _server(monkeypatch, lambda r: httpx.Response(200, text="not json"))
    assert jev.read_turn("hello", AGENTS) is None


def test_an_unknown_agent_is_no_pick(monkeypatch):
    _server(monkeypatch, lambda r: httpx.Response(200, json=_answers(route="supervisor")))
    read = jev.read_turn("hello", AGENTS)
    assert read.route is None and jev.entry_for(read, "chat") == ("chat", False)


# ── the decision ────────────────────────────────────────────────────────────

def _read(route, conf, vision=None):
    return jev.TurnRead(route=route, route_conf=conf, vision=vision, latency_ms=5)


def test_a_confident_pick_enters_that_agent():
    assert jev.entry_for(_read("navigate", 0.97), "chat") == ("navigate", True)
    assert jev.stats["acted"] == 1


def test_an_unsure_pick_changes_nothing(monkeypatch):
    monkeypatch.setenv("LANGROBO_JEV_MIN_CONF", "0.9")
    assert jev.entry_for(_read("navigate", 0.6), "chat") == ("chat", False)
    assert jev.entry_for(None, "local_agent") == ("local_agent", False)


def test_agreeing_with_the_default_is_not_acting():
    assert jev.entry_for(_read("chat", 0.99), "chat") == ("chat", False)
    assert jev.stats["acted"] == 0


def test_outcomes_keep_the_running_agreement():
    jev.record_outcome(_read("navigate", 0.97), "navigate", shadow=True)
    jev.record_outcome(_read("chat", 0.95), "local_agent", shadow=True)
    jev.record_outcome(_read("chat", 0.5), "chat", shadow=True)
    s = jev.status()
    assert s["agree"] == 2 and s["disagree"] == 1 and s["agreement"] == 0.667
    assert s["confident"] == 2 and s["confident_agreement"] == 0.5
    assert jev.record_outcome(None, "chat", shadow=True) is None


def test_is_finished(monkeypatch):
    seen = _server(monkeypatch, lambda r: httpx.Response(200, json={
        "answers": {"done": {"type": "noul", "noul": 0.95}}}))
    assert jev.is_finished("what do you think about it") == 0.95
    assert seen[0]["body"]["questions"]["done"]["type"] == "noul"


# ── the graph's vision check ────────────────────────────────────────────────

def test_a_near_sure_jev_vision_read_backs_up_the_regex():
    from langrobo_core.graph import build as b
    state = {"messages": [HumanMessage(content="anything odd on the sofa?")],
             "agent_run_counts": {}}
    out = {"messages": [AIMessage(content="I think it's fine.")]}
    assert b._vision_backstop("chat", state, out) is None, "the regex alone misses it"
    state["jev_vision"] = 0.95
    cmd = b._vision_backstop("chat", state, out)
    assert cmd is not None and cmd.goto == "local_agent"
    state["jev_vision"] = 0.5
    assert b._vision_backstop("chat", state, out) is None


# ── what a confident pick saves, on the real graph ──────────────────────────

class _Scripted:
    """A model per agent: chat hands over; navigate calls its tool, then answers."""

    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, *a, **k):
        from langchain_core.messages import ToolMessage
        self.calls.append(self.name)
        if self.name == "chat":
            return AIMessage(content="", tool_calls=[{
                "name": "handover", "id": "h1",
                "args": {"next_agent": "navigate", "reason": "a place question", "chain": True}}])
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="I can go to the kitchen.")
        return AIMessage(content="", tool_calls=[{
            "name": "list_saved_locations", "id": "t1", "args": {}}])


@pytest.mark.parametrize("entry, expected_calls", [
    ("chat", ["chat", "navigate", "navigate"]),     # today: chat decides to hand over
    ("navigate", ["navigate", "navigate"]),         # Jev picked navigate: one call saved
])
def test_a_confident_pick_saves_the_handover_call(monkeypatch, entry, expected_calls):
    from langrobo_core.agents import factory
    from langrobo_core.bridges import StubBridge
    from langrobo_core.graph import build_graph
    from langrobo_core.tools import _bridge
    _bridge._instance = None
    _bridge.init(StubBridge())
    calls = []
    fakes = {n: _Scripted(n, calls) for n in AGENTS}
    monkeypatch.setattr(factory, "get_llm", lambda name, **kw: fakes[name])
    out = build_graph().invoke({"messages": [HumanMessage(content="where can you go?")],
                                "active_agent": entry})
    assert calls == expected_calls
    assert out["messages"][-1].content == "I can go to the kitchen."
    assert out["active_agent"] == "navigate"
