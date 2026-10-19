"""Smoke tests — the off-robot safety net for the re-layout.

Everything here runs with no robot, no LLM server, and no API keys: the graph
builds and wires every agent, tool sets bind, and the routing helpers behave.
"""

from langchain_core.messages import AIMessage, HumanMessage

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import (
    _bridge,
    CHAT_TOOLS,
    LOCAL_AGENT_TOOLS,
    NAVIGATE_TOOLS,
)

_bridge._instance = None
_bridge.init(StubBridge())

EXPECTED_AGENTS = {"chat", "local_agent", "navigate"}


def test_graph_builds_with_all_agents():
    from langrobo_core.graph import build_graph
    graph = build_graph()
    nodes = set(graph.get_graph().nodes)
    assert EXPECTED_AGENTS <= nodes
    assert {"turn_entry", "handle_handover"} <= nodes
    # every agent has its ToolNode
    assert {f"{a}_tools" for a in EXPECTED_AGENTS} <= nodes


def test_registry_covers_all_routable_agents():
    from langrobo_core.registry import AGENTS
    # supervisor routes; it is not itself a routing target in the registry
    assert set(AGENTS) == EXPECTED_AGENTS - {"supervisor"}
    for name, meta in AGENTS.items():
        assert meta["description"], name
        assert meta["examples"], name


def test_handover_enum_matches_registry():
    from langrobo_core.tools.handover import handover
    enum = set(handover.args_schema.model_json_schema()
               ["properties"]["next_agent"]["enum"])
    assert enum == EXPECTED_AGENTS


def test_tool_sets_bind_and_have_handover():
    for tools in (CHAT_TOOLS, LOCAL_AGENT_TOOLS, NAVIGATE_TOOLS):
        names = [t.name for t in tools]
        assert "handover" in names
        assert len(names) == len(set(names)), f"duplicate tool in {names}"


def test_chat_owns_the_things_it_must_not_hand_over():
    """chat's prompt promises it never hands over for these. If a tool leaves
    the set, the prompt becomes a lie and the model routes into a dead end.

    tavily_search is excluded on purpose: WEB_TOOLS is empty without
    TAVILY_API_KEY (missing keys degrade, never crash — CLAUDE.md rule 4), so
    asserting on it here would fail on any machine without the key."""
    names = [t.name for t in CHAT_TOOLS]
    for expected in ("get_current_time", "get_robot_status",
                     "send_telegram_message", "send_telegram_photo"):
        assert expected in names


def test_only_local_agent_can_see():
    """look() is the single camera entry point. Two agents holding it would
    both claim to see, and only one of them gets keep_images."""
    from langrobo_core.registry import SPECS
    holders = {name for name, spec in SPECS.items()
               if any(t.name == "look" for t in spec.tools)}
    assert holders == {"local_agent"}
    assert SPECS["local_agent"].keep_images


def test_every_agent_has_its_own_kv_slot():
    """Two agents sharing a slot evict each other's prompt prefix on every
    turn — ~18-50s of re-prefill, and invisible at runtime."""
    from langrobo_core.registry import SLOTS
    assert len(set(SLOTS.values())) == len(SLOTS), SLOTS
    assert set(SLOTS) == EXPECTED_AGENTS


def test_turn_entry_routing():
    from langrobo_core.graph.turn_entry import turn_entry_node

    def target(incoming):
        return turn_entry_node({"messages": [], "active_agent": incoming}).goto

    assert target("chat") == "chat"                 # sticky
    assert target("local_agent") == "local_agent"   # sticky
    assert target("navigate") == "navigate"         # sticky since 2026-09-08
    assert target(None) == "chat"                   # fresh turn default
    assert target("supervisor") == "chat"           # unknown/removed → default


def test_loop_guard_ends_turn_without_llm():
    from langrobo_core.graph.handover_resolver import handle_handover
    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{
                "name": "handover",
                "args": {"next_agent": "navigate"}, "id": "c1", "type": "tool_call"}]),
        ],
        "active_agent": "chat",
        "agent_turn_visits": {"navigate": 99},   # way past the cap
    }
    # ToolMessage carrying the handover payload
    from langchain_core.messages import ToolMessage
    state["messages"].append(ToolMessage(
        content='{"next_agent": "navigate", "reason": "r", "chain": false}',
        name="handover", tool_call_id="c1"))
    out = handle_handover(state)
    assert isinstance(out, dict)                    # plain END update, no Command
    assert out["messages"][0].content               # spoken fallback text


def _bad_handover_state(active_agent):
    """State after a model hands over to a nonexistent agent: the ToolNode
    rejects the enum violation, so the handover ToolMessage carries the
    validation-error text instead of a routing payload."""
    from langchain_core.messages import ToolMessage
    return {
        "messages": [
            HumanMessage(content="order me a pizza"),
            AIMessage(content="", tool_calls=[{
                "name": "handover",
                "args": {"next_agent": "pizza_agent"}, "id": "c1",
                "type": "tool_call"}]),
            ToolMessage(
                content="Error invoking tool 'handover' with kwargs "
                        "{'next_agent': 'pizza_agent'} with error:\n next_agent: "
                        "Input should be 'supervisor', 'chat', ...",
                name="handover", tool_call_id="c1"),
        ],
        "active_agent": active_agent,
        "agent_turn_visits": {},
    }


def test_unknown_handover_reroutes_to_chat():
    """A hallucinated agent name (cloud fallback — enums advisory) must not end
    the turn silently: langgraph ignores an unknown goto channel. The resolver
    reroutes to chat so the user always gets an answer."""
    from langgraph.types import Command
    from langrobo_core.graph.handover_resolver import handle_handover

    # From a specialist: chain straight to chat
    out = handle_handover(_bad_handover_state("navigate"))
    assert isinstance(out, Command) and out.goto == "chat"

    # From chat itself: self-handover nudge takes over — still re-enters chat
    out = handle_handover(_bad_handover_state("chat"))
    assert isinstance(out, Command) and out.goto == "chat"


def test_speech_stream_sentence_split():
    from langrobo_core.utils.speech_stream import split_sentences
    ready, rest = split_sentences("Hello there. How are you doing today? I am fi")
    assert ready == ["Hello there.", "How are you doing today?"]
    assert rest == "I am fi"


def test_no_agent_binds_a_tool_for_hardware_that_is_not_there():
    """Every tool set is shipped to the model on every turn, so a tool that
    cannot work is prompt tokens plus a promise the robot then breaks.

    These four were bound to agents for months while nothing on the rover
    published the topics behind them — see INTEGRATION_GAPS.md §1."""
    from langrobo_core.registry import SPECS
    gone = {"play_music", "stop_music", "pause_music", "resume_music",
            "set_music_volume", "watch_home", "approach_object",
            "navigate_to_visible_object", "where_is", "recall_memory"}
    for name, spec in SPECS.items():
        bound = {t.name for t in spec.tools}
        assert not (bound & gone), f"{name} still binds {bound & gone}"


def test_agents_package_imports_before_the_graph():
    """`import langrobo_core.agents` must work on its own.

    agents/ must never import graph/ — the graph imports the agents. A single
    type annotation reaching the other way (agents.supervisor -> graph.state)
    made this exact import blow up with a partially-initialised module, and it
    only showed when something imported the agents package first.
    """
    import subprocess
    import sys
    subprocess.run(
        [sys.executable, "-c",
         "import langrobo_core.agents as a; assert a.NODES and a.BUILD_LLM_CALLS"],
        check=True,
    )
