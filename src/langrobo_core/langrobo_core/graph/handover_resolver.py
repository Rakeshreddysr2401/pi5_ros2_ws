"""handle_handover — single centralized handover resolver.

Decision logic:
  agent was silent (no AI text) OR chain=True  →  Command(goto=next_agent)  [immediate]
  agent spoke AND chain=False                  →  dict update + END          [sticky]
"""

import json
import logging
from typing import Literal, NamedTuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from .state import AgentState
from ..agent_ids import ROUTABLE
from ..registry import AGENTS

logger = logging.getLogger(__name__)

_MAX_VISITS_PER_AGENT = 3

_ROUTABLE = set(ROUTABLE)


def _parse_handover(content: str) -> tuple[str, str, bool]:
    try:
        data = json.loads(content)
        return data["next_agent"], data.get("reason", ""), bool(data.get("chain", False))
    except (json.JSONDecodeError, KeyError):
        parts = content.split("|", 2)
        agent = parts[0].strip()
        reason = parts[1].strip() if len(parts) > 1 else ""
        chain = parts[2].strip().lower() == "true" if len(parts) > 2 else False
        return agent, reason, chain


def _extract_handover_context(state: AgentState) -> tuple[str, str, bool, str]:
    next_agent = reason = ai_content = ""
    chain = False
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage) and msg.name == "handover":
            next_agent, reason, chain = _parse_handover(msg.content)
        elif isinstance(msg, AIMessage):
            ai_content = msg.content if isinstance(msg.content, str) else ""
            break
    return next_agent, reason, chain, ai_content


def last_user_query(state: AgentState) -> str:
    """The text of this turn's real user message -- the last HumanMessage in
    history, regardless of how many handover hops came after it. Public: also
    used by graph/build.py's vision-question backstop, which needs the same
    "what did the person actually ask" signal outside a handover."""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
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
    # message_utils). Word each note so a stale one reads as a past event
    # ("control passed"), never as a standing instruction to whoever reads it.
    if raw_next == "chat" and reason == "cannot_answer":
        # chat is the default responder AND the router, so "I could not answer
        # this" lands there. Name the agent that gave up, or chat routes
        # straight back to it and the loop guard has to end the turn.
        content = (
            f"[Routing note] The '{current}' agent could not answer the user's "
            f"question. Do NOT route back to '{current}'. Answer it yourself or "
            f"try a different agent.\n"
            f"The user asked: \"{user_query}\""
        )
    else:
        meta = AGENTS.get(raw_next)
        desc = meta["description"] if meta else raw_next
        content = (
            f"[Routing note] Control passed to the '{raw_next}' agent ({desc}) "
            f"because: \"{reason or 'user request'}\". "
            f"The user said: \"{user_query}\". "
            f"The '{raw_next}' agent handles this directly without re-asking "
            f"what was already provided."
        )

    logger.info("Handover: %s → %s (reason: %s)", current, raw_next, reason or "normal")
    return _Resolution(next_agent=raw_next, bridge_messages=[SystemMessage(content=content)])


def handle_handover(state: AgentState) -> Command[Literal[ROUTABLE]] | dict:  # type: ignore[valid-type]
    next_agent, reason, chain, ai_content = _extract_handover_context(state)

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
        chain, ai_content = True, ""
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
        chain, ai_content = True, ""

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

    should_chain = chain or not ai_content

    # Only the bridge note is added. The agent's spoken text already lives in
    # its original AIMessage (the one carrying the handover tool_call) — the
    # projection keeps that message with the tool_call stripped, so adding a
    # copy here would duplicate the utterance in every later prompt.
    messages_to_add = res.bridge_messages

    state_update = {
        "active_agent": res.next_agent,
        "agent_turn_visits": visits,
        "messages": messages_to_add,
    }

    if should_chain:
        logger.info("handle_handover: chaining → %s", res.next_agent)
        return Command(goto=res.next_agent, update=state_update)
    else:
        logger.info("handle_handover: sticky → %s (responds next turn)", res.next_agent)
        return state_update
