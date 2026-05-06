"""Router node — classifies user intent and sets state["intent"].

Two-stage approach (fast path first, LLM fallback):
  1. Keyword matching — no LLM call, no latency
  2. LLM classification — for ambiguous messages

Pattern mirrors owp_agent's supervisor node which decides which
specialist agent to hand off to.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from ..llm import get_llm
from ..prompts import router_prompt
from ..state import AgentState

_NAVIGATE_KW = {
    "go to", "move", "forward", "backward", "back up", "turn", "rotate",
    "find", "navigate", "approach", "come here", "follow", "stop",
    "left", "right", "f:", "b:", "l:", "r:",
}
_VISION_KW = {
    "see", "look", "camera", "what is", "what's", "describe",
    "show me", "in front", "around", "detect", "identify", "spot",
    "colour", "color", "shape", "who is",
}
_STATUS_KW = {
    "battery", "status", "charge", "what are you doing", "how are you",
    "hardware", "sensor", "connection", "mode",
}


def router_node(state: AgentState) -> dict:
    # Grab the most recent human message
    last_human: str | None = None
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_human = (
                msg.content if isinstance(msg.content, str)
                else " ".join(
                    p.get("text", "") for p in msg.content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            )
            break

    if not last_human:
        return {"intent": "chat"}

    text = last_human.lower()

    # ── Fast-path keyword routing ──────────────────────────────────────────
    if any(kw in text for kw in _NAVIGATE_KW):
        return {"intent": "navigate"}
    if any(kw in text for kw in _VISION_KW):
        return {"intent": "vision"}
    if any(kw in text for kw in _STATUS_KW):
        return {"intent": "status"}

    # ── LLM fallback for ambiguous messages ───────────────────────────────
    llm = get_llm()
    response = llm.invoke([
        SystemMessage(content=router_prompt),
        HumanMessage(content=last_human),
    ])
    intent = response.content.strip().lower().split()[0]
    valid  = {"chat", "vision", "navigate", "status"}
    return {"intent": intent if intent in valid else "chat"}
