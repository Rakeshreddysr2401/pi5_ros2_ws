"""StateGraph topology — the only file that knows how nodes connect.

Architecture:
  START → turn_entry → supervisor → supervisor_tools
                                         ↓ (handover)
                                   handle_handover → [chat|local_agent|navigate|status|swiggy|instamart|dineout|tracker|knowledge|briefing]
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
from .turn_entry import turn_entry_node
from .handover_resolver import handle_handover
from ..agents.supervisor import supervisor_node
from ..agents.chat import chat_node
from ..agents.local_agent import local_agent_node
from ..agents.navigate import navigate_node
from ..agents.status import status_node
from ..agents.swiggy import swiggy_node
from ..agents.instamart import instamart_node
from ..agents.dineout import dineout_node
from ..agents.tracker import tracker_node
from ..agents.knowledge import knowledge_node
from ..agents.briefing import briefing_node
from ..tools import (
    SUPERVISOR_TOOLS,
    CHAT_TOOLS,
    LOCAL_AGENT_TOOLS,
    NAVIGATE_TOOLS,
    STATUS_TOOLS,
    SWIGGY_TOOLS,
    INSTAMART_TOOLS,
    DINEOUT_TOOLS,
    TRACKER_TOOLS,
    KNOWLEDGE_AGENT_TOOLS,
    BRIEFING_TOOLS,
)

# Agent registry — the single source of truth for the graph's agents. Each entry
# maps an agent name to (node function, tool set). Everything below is derived from
# this: the loop-guarded agent node, its ToolNode, and its edges. To add an agent,
# add one line here (plus its node module and an entry in nodes/_registry.py so the
# supervisor knows how to route to it) — no other change to this file is needed.
_AGENT_SPECS = {
    "supervisor":  (supervisor_node,  SUPERVISOR_TOOLS),
    "chat":        (chat_node,        CHAT_TOOLS),
    "local_agent": (local_agent_node, LOCAL_AGENT_TOOLS),
    "navigate":    (navigate_node,    NAVIGATE_TOOLS),
    "status":      (status_node,      STATUS_TOOLS),
    "swiggy":      (swiggy_node,      SWIGGY_TOOLS),
    "instamart":   (instamart_node,   INSTAMART_TOOLS),
    "dineout":     (dineout_node,     DINEOUT_TOOLS),
    "tracker":     (tracker_node,     TRACKER_TOOLS),
    "knowledge":   (knowledge_node,   KNOWLEDGE_AGENT_TOOLS),
    "briefing":    (briefing_node,    BRIEFING_TOOLS),
}

# Max times a single agent node may execute within one user turn. Legitimate
# multi-step flows (navigate doing several moves, swiggy search→menu→cart→order)
# stay well under this; a degenerate self-loop (e.g. an agent re-calling the same
# tool because its real tools are unavailable) trips it and ends the turn cleanly.
_MAX_AGENT_RUNS_PER_TURN = 8


# ── Loop guard ──────────────────────────────────────────────────────────────────

def _loop_guarded(agent_name: str, node_fn):
    """Wrap an agent node so it can't loop on its own tools indefinitely.

    Counts this agent's executions per turn in state['agent_run_counts']. Past the
    cap, short-circuits with a plain fallback reply instead of calling the LLM again
    — the turn then ends via _route_after_agent (no tool_calls → END)."""
    def wrapped(state: AgentState) -> dict:
        counts = dict(state.get("agent_run_counts") or {})
        counts[agent_name] = counts.get(agent_name, 0) + 1
        if counts[agent_name] > _MAX_AGENT_RUNS_PER_TURN:
            return {
                "active_agent": agent_name,
                "agent_run_counts": counts,
                "messages": [AIMessage(content=(
                    "Sorry, I got stuck trying to do that. Could you rephrase your request?"
                ))],
            }
        out = dict(node_fn(state) or {})
        out["agent_run_counts"] = counts
        return out

    wrapped.__name__ = f"{agent_name}_guarded"
    return wrapped


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

    # Per agent: a loop-guarded agent node, its ToolNode, and the edges between them.
    for name, (node_fn, tools) in _AGENT_SPECS.items():
        builder.add_node(name, _loop_guarded(name, node_fn))
        builder.add_node(f"{name}_tools", ToolNode(tools=tools))

        # agent → its tools (if it called any) or END
        builder.add_conditional_edges(
            name,
            _route_after_agent,
            {"tools": f"{name}_tools", END: END},
        )
        # tools → handle_handover (if a handover ran) or back to the same agent
        builder.add_conditional_edges(
            f"{name}_tools",
            lambda s, a=name: _route_after_tools(s, a),
            {"handle_handover": "handle_handover", name: name},
        )

    # Entry
    builder.add_edge(START, "turn_entry")
    # turn_entry routes via Command — no static edge needed

    # handle_handover → END (sticky) or Command(goto=agent) (chain) — Command handles routing
    builder.add_edge("handle_handover", END)

    return builder.compile(checkpointer=checkpointer)
