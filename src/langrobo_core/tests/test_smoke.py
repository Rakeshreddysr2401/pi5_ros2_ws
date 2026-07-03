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
    STATUS_TOOLS,
    SUPERVISOR_TOOLS,
    SWIGGY_TOOLS,
    TRACKER_TOOLS,
)

_bridge._instance = None
_bridge.init(StubBridge())

EXPECTED_AGENTS = {"supervisor", "chat", "local_agent", "navigate",
                   "status", "swiggy", "tracker"}


def test_graph_builds_with_all_agents():
    from langrobo_core.graph import build_graph
    graph = build_graph()
    nodes = set(graph.get_graph().nodes)
    assert EXPECTED_AGENTS <= nodes
    assert {"turn_entry", "handle_handover"} <= nodes
    # every agent has its ToolNode
    assert {f"{a}_tools" for a in EXPECTED_AGENTS} <= nodes


def test_registry_covers_all_routable_agents():
    from langrobo_core.graph.registry import AGENTS
    # supervisor routes; it is not itself a routing target in the registry
    assert set(AGENTS) == EXPECTED_AGENTS - {"supervisor"}
    for name, meta in AGENTS.items():
        assert meta["description"], name
        assert meta["examples"], name


def test_handover_enum_matches_registry():
    from langrobo_core.tools.handover import handover
    enum = set(handover.args_schema.model_json_schema()
               ["properties"]["next_agent"]["enum"])
    assert enum == EXPECTED_AGENTS | {"supervisor"}


def test_tool_sets_bind_and_have_handover():
    for tools in (CHAT_TOOLS, LOCAL_AGENT_TOOLS, NAVIGATE_TOOLS, STATUS_TOOLS,
                  SUPERVISOR_TOOLS, SWIGGY_TOOLS, TRACKER_TOOLS):
        names = [t.name for t in tools]
        assert "handover" in names
        assert len(names) == len(set(names)), f"duplicate tool in {names}"


def test_chat_has_memory_and_household_tools():
    names = [t.name for t in CHAT_TOOLS]
    for expected in ("recall_memory", "remember", "forget", "update_list",
                     "set_reminder", "get_current_time"):
        assert expected in names


def test_turn_entry_routing():
    from langrobo_core.graph.turn_entry import turn_entry_node

    def target(incoming):
        return turn_entry_node({"messages": [], "active_agent": incoming}).goto

    assert target("supervisor") == "supervisor"     # [SYSTEM] events
    assert target("chat") == "chat"                 # sticky
    assert target("local_agent") == "local_agent"   # sticky
    assert target("navigate") == "chat"             # specialists not sticky
    assert target(None) == "chat"                   # fresh turn default


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


def test_speech_stream_sentence_split():
    from langrobo_core.utils.speech_stream import split_sentences
    ready, rest = split_sentences("Hello there. How are you doing today? I am fi")
    assert ready == ["Hello there.", "How are you doing today?"]
    assert rest == "I am fi"


def test_chat_has_music_tools():
    names = [t.name for t in CHAT_TOOLS]
    for expected in ("play_music", "stop_music", "pause_music",
                     "resume_music", "set_music_volume"):
        assert expected in names


def test_music_tools_against_stub():
    from langrobo_core.tools.music import music_context, play_music, stop_music
    result = play_music.invoke({"query": "calm piano"})
    assert "calm piano" in result
    assert "NOW PLAYING" in music_context()
    assert "stopped" in stop_music.invoke({}).lower()
    assert music_context() == ""
