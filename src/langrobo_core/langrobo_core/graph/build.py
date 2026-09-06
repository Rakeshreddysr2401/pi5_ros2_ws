"""StateGraph topology — the only file that knows how nodes connect.

Architecture:
  START → turn_entry → [chat | local_agent | navigate]
                              ↓
                        per-agent tools
                              ↓ (if a handover ran)
                        handle_handover → another agent, or END
                              ↓ (no tool calls left: the agent answered)
                             END (that agent becomes sticky for the next turn)

  turn_entry picks the entry directly — the sticky agent from last turn, or
  chat. There is no router node: chat is the default responder AND carries the
  routing table, so a fresh turn costs one LLM call, not two.

Every agent below is derived from registry.py — the node, its ToolNode and its
edges all come from one AgentSpec. Adding an agent needs NO edit to this file.
"""

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from ..agents import NODES
from ..registry import SPECS
from .state import AgentState
from .turn_entry import turn_entry_node
from .handover_resolver import handle_handover

# Max times a single agent node may execute within one user turn. Legitimate
# multi-step flows (navigate doing several moves in a row) stay well under this;
# a degenerate self-loop (an agent re-calling the same tool because the one it
# wants is unavailable) trips it and ends the turn cleanly.
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
    langrobo_core.tools._bridge.init(bridge) before calling this.
    """
    builder = StateGraph(AgentState)

    # Utility nodes
    builder.add_node("turn_entry",      turn_entry_node)
    builder.add_node("handle_handover", handle_handover)

    # Per agent: a loop-guarded agent node, its ToolNode, and the edges between them.
    for name, spec in SPECS.items():
        builder.add_node(name, _loop_guarded(name, NODES[name]))
        builder.add_node(f"{name}_tools", ToolNode(tools=spec.tools))

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
