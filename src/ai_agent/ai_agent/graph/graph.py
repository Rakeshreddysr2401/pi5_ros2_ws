"""StateGraph topology — the only file that knows how nodes connect.

Pattern mirrors owp_agent's supervisor_agent.py:
  - Flat graph (no nested subgraphs)
  - Conditional routing via state fields
  - ToolNode shared by all agent nodes; routes back via state["intent"]
"""

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage

from .state import AgentState
from .nodes.router import router_node
from .nodes.chat import chat_node
from .nodes.vision import vision_node
from .nodes.navigate import navigate_node
from .nodes.status import status_node
from .tools import all_tools


# ── Routing helpers ────────────────────────────────────────────────────────────

def _route_by_intent(state: AgentState) -> str:
    return state.get("intent") or "chat"


def _route_to_tools_or_end(state: AgentState) -> str:
    """After an agent node: go to tools if there are pending tool calls, else end."""
    last = state["messages"][-1] if state["messages"] else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


def _route_after_tools(state: AgentState) -> str:
    """After tool execution: return to the node that owns this intent."""
    return state.get("intent") or "chat"


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_graph():
    """Build and compile the robot brain StateGraph.

    Called once at startup.  The bridge must be initialised via
    graph.tools._bridge.init() before calling this.
    """
    builder = StateGraph(AgentState)

    # Nodes
    builder.add_node("router",   router_node)
    builder.add_node("chat",     chat_node)
    builder.add_node("vision",   vision_node)
    builder.add_node("navigate", navigate_node)
    builder.add_node("status",   status_node)
    builder.add_node("tools",    ToolNode(all_tools))

    # Entry
    builder.add_edge(START, "router")

    # router → agent node (based on detected intent)
    builder.add_conditional_edges(
        "router",
        _route_by_intent,
        {
            "chat":     "chat",
            "vision":   "vision",
            "navigate": "navigate",
            "status":   "status",
        },
    )

    # agent node → tools or END
    for node_name in ("chat", "vision", "navigate", "status"):
        builder.add_conditional_edges(
            node_name,
            _route_to_tools_or_end,
            {"tools": "tools", END: END},
        )

    # tools → back to originating agent node
    builder.add_conditional_edges(
        "tools",
        _route_after_tools,
        {
            "chat":     "chat",
            "vision":   "vision",
            "navigate": "navigate",
            "status":   "status",
        },
    )

    return builder.compile()
