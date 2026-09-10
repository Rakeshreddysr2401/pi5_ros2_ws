"""StateGraph topology — the only file that knows how nodes connect.

Architecture:
  START → turn_entry → [chat | local_agent | navigate]
                              ↓
                        per-agent tools
                              ↓ (if a handover ran)
                        handle_handover → another agent, or END
                              ↓ (no tool calls left: the agent answered)
                       vision backstop → local_agent, or END
                              ↓ (that agent becomes sticky for the next turn)

  turn_entry picks the entry directly — the sticky agent from last turn, or
  chat. There is no router node: chat is the default responder AND carries the
  routing table, so a fresh turn costs one LLM call, not two.

Every agent below is derived from registry.py — the node, its ToolNode and its
edges all come from one AgentSpec. Adding an agent needs NO edit to this file.
"""

import logging
import re

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from ..agents import NODES
from ..registry import SPECS
from .state import AgentState
from .turn_entry import turn_entry_node
from .handover_resolver import handle_handover, last_user_query

logger = logging.getLogger(__name__)

# Max times a single agent node may execute within one user turn. Legitimate
# multi-step flows (navigate doing several moves in a row) stay well under this;
# a degenerate self-loop (an agent re-calling the same tool because the one it
# wants is unavailable) trips it and ends the turn cleanly.
_MAX_AGENT_RUNS_PER_TURN = 8


# ── Vision-question backstop ─────────────────────────────────────────────────
#
# CHAT_PROMPT and NAVIGATE_PROMPT both say, in as many words: a question about
# what the robot sees is not yours to answer, hand over to local_agent. Both
# are INSTRUCTIONS. A real transcript (rover repo PERCEPTION_STATE.md,
# 2026-09-10) shows the Mac mini's 12B model ignoring them: with `navigate`
# sticky from an earlier move (registry.py, since 2026-09-08), "what are you
# looking at" was answered directly -- in plausible, specific-sounding prose,
# by an agent that has no camera tool and no image anywhere in its context.
# local_agent was never entered. No amount of stamping look()'s frame (see
# LOCAL_AGENT_PROMPT's "IS YOUR VIEW STILL GOOD?" block) helps a question that
# never reaches local_agent in the first place.
#
# This is the enforcement half: a deterministic check, not a louder prompt.
# Narrow on purpose, matched against the USER'S OWN WORDS rather than the
# model's free-form reply -- parsing what the model claims to have seen is
# exactly the kind of pattern-matching a 12B model is already failing at, and
# the user's intent is fixed text regardless of how the agent responds to it.
# Deliberately excludes "take/send a photo" phrasing: chat legitimately owns
# send_telegram_photo (which grabs a genuinely fresh frame, see its docstring)
# and correctly answers those with a tool call -- this backstop only fires on
# a NO-tool-call ending, so a real send_telegram_photo turn never reaches it.
_VISION_QUESTION = re.compile(
    r"\b(what (are you|do you|can you) (currently )?(looking at|see|seeing)"
    r"|what.?s? in front of (you|it)"
    r"|is (it|he|she|that|there) still there"
    r"|look(ing)? again"
    r"|describe (what|the) (you )?(see|room|scene|surroundings))\b",
    re.IGNORECASE,
)


def _vision_backstop(agent_name: str, state: AgentState, out: dict) -> Command | None:
    """None if nothing is wrong; a Command chaining to local_agent if this
    agent just answered a vision question it should have handed off instead.

    Fires only when ALL of: this agent is not local_agent; it is about to END
    the turn by speaking (its own AIMessage carries no tool call); local_agent
    has not already run this turn (so a real answer from it is never
    overridden, and this cannot re-trigger on local_agent's own reply); and
    the user's own last message matches _VISION_QUESTION.

    The wrong AIMessage is NOT removed -- utils/message_utils' append-only
    cache invariant applies here exactly as it does to a stale image, and
    besides, agent_node only speaks the FINAL message once the graph reaches
    END, so this one is never sent to the user. A routing note is appended
    after it, worded the same way handle_handover's notes are (a past event,
    not a standing instruction), and local_agent runs next in the SAME turn.
    """
    if agent_name == "local_agent":
        return None
    msgs = out.get("messages") or []
    last = msgs[-1] if msgs else None
    if not (isinstance(last, AIMessage) and not last.tool_calls and last.content):
        return None
    if (state.get("agent_run_counts") or {}).get("local_agent", 0) > 0:
        return None
    query = last_user_query(state)
    if not _VISION_QUESTION.search(query):
        return None

    logger.warning(
        "Vision backstop: '%s' answered a vision question without handing "
        "over -- forcing local_agent (query: %r)", agent_name, query[:80])
    note = SystemMessage(content=(
        f"[Routing note] Control passed to the 'local_agent' agent because: "
        f"the '{agent_name}' agent answered a visual question without "
        f"looking. The user said: {query!r}. Call look() and answer from "
        f"the real image \u2014 do not repeat what was already said."
    ))
    return Command(goto="local_agent", update={
        "active_agent": "local_agent",
        "messages": [note],
    })


# ── Loop guard ──────────────────────────────────────────────────────────────────

def _loop_guarded(agent_name: str, node_fn):
    """Wrap an agent node so it can't loop on its own tools indefinitely, and
    so a vision question it should have handed off does not stand as the
    answer (see _vision_backstop).

    Counts this agent's executions per turn in state['agent_run_counts']. Past the
    cap, short-circuits with a plain fallback reply instead of calling the LLM again
    — the turn then ends via _route_after_agent (no tool_calls → END)."""
    def wrapped(state: AgentState) -> dict | Command:
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

        redirect = _vision_backstop(agent_name, state, out)
        if redirect is not None:
            redirect.update["agent_run_counts"] = counts
            return redirect

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
