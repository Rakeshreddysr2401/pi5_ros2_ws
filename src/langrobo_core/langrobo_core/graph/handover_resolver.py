"""handle_handover — single centralized handover resolver.

Every handover chains: Command(goto=next_agent), and the target answers in the
same turn. (Until 2026-10-21 a handover with spoken text and chain=False ended
the turn instead -- the user heard "Let me check." and nothing was done.)
"""

import json
import logging
from typing import Literal, NamedTuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from .state import AgentState
from ..agent_ids import ROUTABLE
from ..utils.history import is_camera_frame

logger = logging.getLogger(__name__)

_MAX_VISITS_PER_AGENT = 3

_ROUTABLE = set(ROUTABLE)


def _parse_handover(content: str) -> tuple[str, str]:
    try:
        data = json.loads(content)
        return data["next_agent"], data.get("reason", "")
    except (json.JSONDecodeError, KeyError):
        parts = content.split("|", 2)
        return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def _extract_handover(state: AgentState) -> tuple[str, str]:
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage) and msg.name == "handover":
            return _parse_handover(msg.content)
        if isinstance(msg, AIMessage):
            break
    return "", ""


def last_user_query(state: AgentState) -> str:
    """The text of this turn's real user message -- the last HumanMessage in
    history, regardless of how many handover hops came after it. Public: also
    used by graph/build.py's vision-question backstop, which needs the same
    "what did the person actually ask" signal outside a handover.

    Skips look()'s camera frames: they are HumanMessages too (images only
    reach llama.cpp in user-role messages), so "look, then go near the box"
    used to hand navigate the note 'The user said: "[Camera view — taken
    at ...]"' instead of the request."""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage) and not is_camera_frame(msg):
            c = msg.content
            return c if isinstance(c, str) else " ".join(
                p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"
            )
    return ""


class _Resolution(NamedTuple):
    next_agent: str
    bridge_messages: list


def _resolve(state: AgentState, raw_next: str, reason: str) -> _Resolution:
    current = state.get("active_agent", "chat")
    user_query = last_user_query(state)

    # Routing notes are PERMANENT history: the projection keeps every historical
    # SystemMessage so cached prompt prefixes never diverge mid-history (see
    # message_utils). So: short, and worded as a past event ("control passed"),
    # never as a standing instruction to whoever reads it later.
    content = (
        f"[Routing note] Control passed to '{raw_next}' because: "
        f"\"{reason or 'user request'}\". The user said: \"{user_query}\". "
        f"Act on it without re-asking."
    )

    logger.info("Handover: %s → %s (reason: %s)", current, raw_next, reason or "normal")
    return _Resolution(next_agent=raw_next, bridge_messages=[SystemMessage(content=content)])


def handle_handover(state: AgentState) -> Command[Literal[ROUTABLE]] | dict:  # type: ignore[valid-type]
    next_agent, reason = _extract_handover(state)

    if not next_agent:
        logger.warning("handle_handover: no handover tool message found — "
                       "falling back to chat")
        return Command(goto="chat", update={})

    # A hallucinated target (possible on the cloud fallback — schema enums are
    # advisory there) or a ToolNode validation-error payload must never become
    # a graph goto: langgraph ignores an unknown channel and the turn ends
    # SILENTLY. Reroute to chat so the user always gets an answer.
    if next_agent not in _ROUTABLE:
        logger.warning("Handover to unknown agent %r — rerouting to chat",
                       next_agent[:80])
        res = _Resolution(
            next_agent="chat",
            bridge_messages=[SystemMessage(content=(
                f"[Routing note] A handover requested a non-existent agent "
                f"({next_agent[:60]!r}). Control passed to 'chat' to answer the "
                f"user directly: \"{last_user_query(state)}\""
            ))],
        )
    else:
        res = _resolve(state, next_agent, reason)
    current = state.get("active_agent", "chat")

    # Self-handover: an agent routed to itself instead of answering. Re-enter it
    # once with a hard "answer now" nudge (counts toward the loop budget below).
    if res.next_agent == current:
        logger.info("Self-handover by '%s' — nudging it to answer directly", current)
        res = _Resolution(
            next_agent=current,
            bridge_messages=[SystemMessage(content=(
                f"[Routing note] The '{current}' agent handed over to itself. "
                f"For this request it must NOT call handover again — it answers the "
                f"user directly in plain text now: \"{last_user_query(state)}\""
            ))],
        )

    visits = dict(state.get("agent_turn_visits") or {})
    visits[res.next_agent] = visits.get(res.next_agent, 0) + 1

    # Loop guard: stop calling the LLM and end the turn deterministically with a
    # plain message (applies to every agent, chat included — no exemptions).
    if visits[res.next_agent] > _MAX_VISITS_PER_AGENT:
        logger.warning(
            "Loop guard: %s visited %d times — ending turn with fallback",
            res.next_agent, visits[res.next_agent],
        )
        return {
            "active_agent": res.next_agent,
            "agent_turn_visits": visits,
            "messages": [AIMessage(content=(
                "Sorry, I'm having trouble with that one. Could you rephrase it for me?"
            ))],
        }

    # Only the bridge note is added. The agent's spoken text already lives in
    # its original AIMessage (the one carrying the handover tool_call).
    logger.info("handle_handover: chaining → %s", res.next_agent)
    return Command(goto=res.next_agent, update={
        "active_agent": res.next_agent,
        "agent_turn_visits": visits,
        "messages": res.bridge_messages,
    })
