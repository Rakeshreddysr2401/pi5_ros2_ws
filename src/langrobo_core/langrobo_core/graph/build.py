"""StateGraph topology — the only file that knows how nodes connect.

Architecture:
  START → turn_entry → [chat | local_agent | navigate]
                              ↓
                        per-agent tools
                              ↓ (if a handover ran)
                        handle_handover → another agent, or END
                              ↓ (no tool calls left: the agent answered)
                vision / movement backstop → local_agent / navigate, or END
                              ↓ (that agent becomes sticky for the next turn)

  turn_entry picks the entry directly — the sticky agent from last turn, or
  chat. There is no router node: chat is the default responder AND carries the
  routing table, so a fresh turn costs one LLM call, not two.

Every agent below is derived from registry.py — the node, its ToolNode and its
edges all come from one AgentSpec. Adding an agent needs NO edit to this file.
"""

import logging
import re
import time
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from ..agents import NODES
from ..registry import SPECS
from .state import AgentState
from .turn_entry import turn_entry_node
from .handover_resolver import handle_handover, last_user_query
from ..utils import pose_stamp
from ..utils.history import is_camera_frame

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
# Deliberately excludes "take/send a photo" phrasing: "please take a new fresh
# image and send it" does not match any alternative below, so chat's own
# correct tool call for it (send_telegram_photo, which grabs a genuinely fresh
# frame -- see its docstring) is never in this backstop's path to begin with.
#
# FIRST VERSION (2026-09-10 evening) only checked "spoke with no tool call at
# all" -- and missed a second shape of the SAME bug the same evening: asked
# "now what are you looking at" with navigate sticky, the model called
# scan_surroundings() -- a real 360-degree physical rotation -- instead of
# handing over. That is not the agent doing its job; nothing in "what are you
# looking at" asked it to move. So the check below is keyed on the one tool
# call that IS always correct here -- handover -- not on "any tool call is
# fine": anything else (move_robot, navigate_to_pose, scan_surroundings,
# approach_described_object, or plain unrouted text) is the same failure.
_VISION_QUESTION = re.compile(
    r"\b(what (are you|do you|can you) (currently )?(looking at|see|seeing)"
    r"|what.?s? in front of (you|it)"
    r"|is (it|he|she|that|there) still there"
    r"|look(ing)? again"
    r"|describe (what|the) (you )?(see|room|scene|surroundings))\b",
    re.IGNORECASE,
)


# Jev (services/jev.py) as a second opinion on "is this a vision question?":
# the regex above only knows phrasings someone wrote down. Only when Jev is
# near-sure, and only in LANGROBO_JEV=on (agent_node sets jev_vision then).
JEV_VISION_P = 0.9


def _jev_says_vision(state: AgentState) -> bool:
    p = state.get("jev_vision")
    return p is not None and p >= JEV_VISION_P


def _vision_backstop(agent_name: str, state: AgentState, out: dict) -> Command | None:
    """None if nothing is wrong; a Command chaining to local_agent if this
    agent just tried to handle a vision question itself instead of handing it
    off -- by speaking, or by calling any tool OTHER than handover.

    Fires only when ALL of: this agent is not local_agent; its AIMessage this
    step does not call handover (a handover call is always the correct thing
    here and must pass through untouched); it did SOMETHING (spoke, or called
    some other tool -- an AIMessage with neither is not a completed step and
    is left alone); local_agent has not already run this turn (so a real
    answer from it, or a real look() already in flight, is never overridden);
    and the user's own last message matches _VISION_QUESTION.

    Whatever the agent proposed -- spoken text, or a tool call -- is NOT
    executed and NOT added to history: returning a Command here replaces `out`
    entirely, so a proposed scan_surroundings/move_robot/etc. never reaches
    its ToolNode and the robot never physically moves because of it. A
    routing note is appended instead, worded the same way handle_handover's
    notes are (a past event, not a standing instruction), and local_agent runs
    next in the SAME turn. agent_node only speaks the FINAL message once the
    graph reaches END, so nothing wrong is ever said or driven to the user.
    """
    if agent_name == "local_agent":
        return None
    msgs = out.get("messages") or []
    last = msgs[-1] if msgs else None
    if not isinstance(last, AIMessage):
        return None
    tool_calls = last.tool_calls or []
    if any(tc.get("name") == "handover" for tc in tool_calls):
        return None
    if not tool_calls and not last.content:
        return None
    if (state.get("agent_run_counts") or {}).get("local_agent", 0) > 0:
        return None
    query = last_user_query(state)
    if not (_VISION_QUESTION.search(query) or _jev_says_vision(state)):
        return None

    attempted = ", ".join(tc.get("name", "?") for tc in tool_calls) or "answered directly"
    logger.warning(
        "Vision backstop: '%s' %s instead of handing over -- forcing "
        "local_agent (query: %r)", agent_name, attempted, query[:80])
    note = SystemMessage(content=(
        f"[Routing note] Control passed to the 'local_agent' agent because: "
        f"the '{agent_name}' agent tried to handle a visual question itself "
        f"({attempted}) instead of handing it over. The user said: "
        f"{query!r}. Call look() and answer from the real image."
    ))
    return Command(goto="local_agent", update={
        "active_agent": "local_agent",
        "messages": [note],
    })


# ── Movement-request backstop ────────────────────────────────────────────────
#
# The same failure in the other direction (2026-10-10, real transcript): the
# microphone merged "Small water cool. Pizza is ready. I know you like to
# play. , follow me" into one turn; it entered `chat`, which has no movement
# tool, saved a memory and SAID "I am following you now" -- and the robot
# never moved. A spoken claim of an action nobody took. CHAT_PROMPT already
# routes movement to navigate; a prompt rule is a thing to measure, not a fix
# (CLAUDE.md). So, exactly as the vision backstop above: the user's own words
# ask for movement, the agent that answered cannot move, and it neither handed
# over nor let navigate run -> its reply or tool call is discarded (never
# spoken, never executed) and navigate gets the turn.
#
# Narrow, on the USER'S words: phrasings that ask THIS robot to move. "go to
# the kitchen" yes, "go to sleep" no (the article is required); "come here",
# "follow me", "turn left", "move forward", "go near/out/through ...".
# [SYSTEM] turns are excluded: their text quotes errands ("you were asked to
# go to the kitchen ... NOT done"), and forcing navigate there could re-drive
# a trip that just failed.
_MOTION_REQUEST = re.compile(
    r"\b(follow me|come (to me|here|over here|back (here|to me)|closer)"
    r"|go (to|into|towards?) (the|my|your|that|this)\b"
    r"|go (near|closer|forward|ahead|back(ward)?|outside|inside|out of|through|around)\b"
    r"|move (forward|back(ward)?|ahead|closer|left|right|a (little|bit))"
    r"|turn (left|right|around)"
    r"|drive (to|forward|back))",
    re.IGNORECASE,
)

# the agents that can move the wheels, from their tool sets (registry.py): any
# agent bound to move_robot. Not a hand-kept name list.
_MOVERS = tuple(name for name, spec in SPECS.items()
                if any(getattr(t, "name", "") == "move_robot" for t in spec.tools))


def _motion_backstop(agent_name: str, state: AgentState, out: dict) -> Command | None:
    """None if nothing is wrong; a Command chaining to navigate if an agent
    without a movement tool answered a movement request itself -- by speaking
    or by calling any tool other than handover. Fires only when ALL of: the
    agent is not a mover; its step does not call handover; it did something;
    no mover has run this turn; the turn is the user's (not [SYSTEM]); and the
    user's own last message matches _MOTION_REQUEST. Same discard-and-chain
    as _vision_backstop: nothing wrong is ever said or done."""
    if not _MOVERS or agent_name in _MOVERS:
        return None
    msgs = out.get("messages") or []
    last = msgs[-1] if msgs else None
    if not isinstance(last, AIMessage):
        return None
    tool_calls = last.tool_calls or []
    if any(tc.get("name") == "handover" for tc in tool_calls):
        return None
    if not tool_calls and not last.content:
        return None
    if any((state.get("agent_run_counts") or {}).get(m, 0) > 0 for m in _MOVERS):
        return None
    query = last_user_query(state)
    if "[SYSTEM]" in query or not _MOTION_REQUEST.search(query):
        return None

    attempted = ", ".join(tc.get("name", "?") for tc in tool_calls) or "answered directly"
    logger.warning(
        "Movement backstop: '%s' %s instead of handing over -- forcing "
        "%s (query: %r)", agent_name, attempted, _MOVERS[0], query[:80])
    note = SystemMessage(content=(
        f"[Routing note] Control passed to the '{_MOVERS[0]}' agent because: "
        f"the '{agent_name}' agent tried to handle a movement request itself "
        f"({attempted}) instead of handing it over. The user said: "
        f"{query!r}. Do the movement with your tools, or say why you cannot."
    ))
    return Command(goto=_MOVERS[0], update={
        "active_agent": _MOVERS[0],
        "messages": [note],
    })


# ── Fresh view first ─────────────────────────────────────────────────────────
#
# The second half of "a vision question must be answered from a real image":
# from a CURRENT one. LOCAL_AGENT_PROMPT says to look again when the last view
# is more than a minute old, and every turn carries that view's age -- and on
# 2026-10-02 the model still answered a second "what do you see?" from a
# 73-second-old photo, twice; once from a photo taken before the lights came
# on ("it is very dark"). Same shape as the backstop above: the user's own
# words decide, not the model's judgement.
#
# It runs BEFORE local_agent's LLM call, not after: replies stream to the
# speaker sentence by sentence, so a stale answer overridden afterwards would
# already have been heard. The look() is appended as an ordinary tool call and
# result (append-only history -- the KV cache is untouched), so the model
# simply finds a fresh photo in front of it.
STALE_VIEW_S = 60.0


def _looked_this_turn(state: AgentState) -> bool:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage) and not is_camera_frame(msg):
            return False                       # reached the user's message
        if isinstance(msg, AIMessage) and any(
                tc.get("name") == "look" for tc in (msg.tool_calls or [])):
            return True
    return False


def _take_look(call_id: str) -> list:
    """look() run as the tool call `call_id` would run it: its messages."""
    from ..tools.look import look
    res = look.invoke({"name": "look", "args": {}, "id": call_id, "type": "tool_call"})
    return list(res.update["messages"]) if isinstance(res, Command) else [res]


def _fresh_view_first(agent_name: str, state: AgentState) -> list | None:
    """[look call, its result...] to put in front of local_agent's LLM call,
    or None. Only for a current-view question (_VISION_QUESTION, the user's
    words), only if this turn has not looked yet, and only when the last view
    is older than STALE_VIEW_S (or there is none)."""
    if agent_name != "local_agent":
        return None
    if not _VISION_QUESTION.search(last_user_query(state)):
        return None
    if _looked_this_turn(state):
        return None
    _, when = pose_stamp.last_view()
    if when is not None and time.time() - when <= STALE_VIEW_S:
        return None
    call_id = f"fresh_view_{uuid.uuid4().hex[:12]}"
    logger.info("Fresh view first: the last photo is %s -- looking before local_agent answers",
                "missing" if when is None else f"{time.time() - when:.0f} s old")
    call = AIMessage(content="", tool_calls=[
        {"name": "look", "args": {}, "id": call_id, "type": "tool_call"}])
    return [call] + _take_look(call_id)


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
        pre = _fresh_view_first(agent_name, state)
        if pre:
            state = {**state, "messages": list(state["messages"]) + pre}
        out = dict(node_fn(state) or {})
        if pre:
            out["messages"] = pre + list(out.get("messages") or [])
        out["agent_run_counts"] = counts

        redirect = _vision_backstop(agent_name, state, out) or \
            _motion_backstop(agent_name, state, out)
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
