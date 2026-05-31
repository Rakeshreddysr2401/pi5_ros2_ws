"""handle_handover — single centralized handover resolver.

Decision logic:
  agent was silent (no AI text) OR chain=True  →  Command(goto=next_agent)  [immediate]
  agent spoke AND chain=False                  →  dict update + END          [sticky]
"""

import json
import logging
from typing import NamedTuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from ..state import AgentState

logger = logging.getLogger(__name__)

_MAX_VISITS_PER_AGENT = 3

_AGENT_DESCRIPTIONS = {
    "chat":     "general conversation, web search, system status, small talk",
    "vision":   "visual reasoning, object detection, scene description",
    "navigate": "robot movement, chassis control, navigation to targets",
    "status":   "robot operational state, battery, hardware queries",
    "swiggy":   "food ordering, restaurant search, cart management, placing Swiggy orders",
    "tracker":  "Swiggy delivery tracking, order status, ETA",
}


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


def _last_user_query(state: AgentState) -> str:
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
    current = state.get("active_agent", "supervisor")
    user_query = _last_user_query(state)

    if raw_next == "supervisor":
        if reason == "cannot_answer":
            content = (
                f"The previous agent ('{current}') could not answer the user's question. "
                f"Do NOT route back to '{current}'. Try a different agent.\n"
                f"The user asked: \"{user_query}\""
            )
        else:
            content = "Route to the appropriate agent based on the conversation."
    else:
        desc = _AGENT_DESCRIPTIONS.get(raw_next, raw_next)
        content = (
            f"You are the {raw_next} agent, responsible for: {desc}.\n"
            f"You were called because: \"{reason or 'user request'}\". "
            f"The user said: \"{user_query}\". "
            f"Handle this directly without re-asking what was already provided."
        )

    logger.info("Handover: %s → %s (reason: %s)", current, raw_next, reason or "normal")
    return _Resolution(next_agent=raw_next, bridge_messages=[SystemMessage(content=content)])


def handle_handover(state: AgentState):
    next_agent, reason, chain, ai_content = _extract_handover_context(state)

    if not next_agent:
        logger.warning("handle_handover: no handover tool message found — falling back to supervisor")
        return Command(goto="supervisor", update={})

    res = _resolve(state, next_agent, reason)

    visits = dict(state.get("agent_turn_visits") or {})
    visits[res.next_agent] = visits.get(res.next_agent, 0) + 1

    if visits[res.next_agent] > _MAX_VISITS_PER_AGENT and res.next_agent != "chat":
        logger.warning(
            "Loop guard: %s visited %d times — breaking cycle, redirecting to chat",
            res.next_agent, visits[res.next_agent],
        )
        res = _Resolution(
            next_agent="chat",
            bridge_messages=[SystemMessage(content=(
                "A routing loop was detected. Respond with plain natural language only — "
                "no tool calls. Acknowledge the user's request helpfully."
            ))],
        )
        visits["chat"] = visits.get("chat", 0) + 1

    should_chain = chain or not ai_content

    messages_to_add = (
        ([AIMessage(content=ai_content)] if ai_content else []) + res.bridge_messages
    )

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
