"""StateGraph topology — the only file that knows how nodes connect.

Architecture:
  START → turn_entry → supervisor → supervisor_tools
                                         ↓ (handover)
                                   handle_handover → [chat|vision|navigate|status|swiggy|tracker]
                                                            ↓
                                                      per-agent tools
                                                            ↓ (if handover)
                                                      handle_handover
                                                            ↓ (chain=False, agent spoke)
                                                           END (sticky to next agent)
"""

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from .state import AgentState
from .nodes.turn_entry import turn_entry_node
from .nodes.handle_handover import handle_handover
from .nodes.supervisor import supervisor_node
from .nodes.chat import chat_node
from .nodes.vision import vision_node
from .nodes.navigate import navigate_node
from .nodes.status import status_node
from .nodes.swiggy import swiggy_node
from .nodes.tracker import tracker_node
from .tools import (
    SUPERVISOR_TOOLS,
    CHAT_TOOLS,
    VISION_TOOLS,
    NAVIGATE_TOOLS,
    STATUS_TOOLS,
    SWIGGY_TOOLS,
    TRACKER_TOOLS,
)

_AGENTS = ["supervisor", "chat", "vision", "navigate", "status", "swiggy", "tracker"]


# ── Routing helpers ────────────────────────────────────────────────────────────

def _route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1] if state["messages"] else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


def _route_after_tools(state: AgentState, agent_name: str) -> str:
    """After tool execution: go to handle_handover if a handover tool ran, else loop back."""
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage) and msg.name == "handover":
            return "handle_handover"
        if isinstance(msg, AIMessage):
            break
    return agent_name


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_graph(checkpointer=None):
    """Build and compile the robot brain StateGraph.

    Called once at startup. The bridge must be initialised via
    graph.tools._bridge.init(bridge) before calling this.
    """
    builder = StateGraph(AgentState)

    # Utility nodes
    builder.add_node("turn_entry",      turn_entry_node)
    builder.add_node("handle_handover", handle_handover)

    # Agent nodes
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("chat",       chat_node)
    builder.add_node("vision",     vision_node)
    builder.add_node("navigate",   navigate_node)
    builder.add_node("status",     status_node)
    builder.add_node("swiggy",     swiggy_node)
    builder.add_node("tracker",    tracker_node)

    # Per-agent tool nodes
    builder.add_node("supervisor_tools", ToolNode(tools=SUPERVISOR_TOOLS))
    builder.add_node("chat_tools",       ToolNode(tools=CHAT_TOOLS))
    builder.add_node("vision_tools",     ToolNode(tools=VISION_TOOLS))
    builder.add_node("navigate_tools",   ToolNode(tools=NAVIGATE_TOOLS))
    builder.add_node("status_tools",     ToolNode(tools=STATUS_TOOLS))
    builder.add_node("swiggy_tools",     ToolNode(tools=SWIGGY_TOOLS))
    builder.add_node("tracker_tools",    ToolNode(tools=TRACKER_TOOLS))

    # Entry
    builder.add_edge(START, "turn_entry")
    # turn_entry routes via Command — no static edge needed

    # agent → tools or END
    for agent in _AGENTS:
        builder.add_conditional_edges(
            agent,
            _route_after_agent,
            {"tools": f"{agent}_tools", END: END},
        )

    # tools → handle_handover or back to same agent
    for agent in _AGENTS:
        builder.add_conditional_edges(
            f"{agent}_tools",
            lambda s, a=agent: _route_after_tools(s, a),
            {"handle_handover": "handle_handover", agent: agent},
        )

    # handle_handover → END (sticky) or Command(goto=agent) (chain) — Command handles routing
    builder.add_edge("handle_handover", END)

    return builder.compile(checkpointer=checkpointer)
