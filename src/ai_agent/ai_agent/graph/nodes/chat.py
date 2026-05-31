"""Chat agent — general conversation, web search, system status, small talk."""

import logging

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..state import AgentState
from ..tools import CHAT_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = """\
You are a friendly home assistant robot. Answer the user naturally and concisely.

== TOOLS ==
  speak(text)              — say something to the user via the speaker
  query_vision(question)   — ask the camera a specific visual question
  get_detected_objects()   — get a list of nearby detected objects
  get_robot_status()       — check battery, hardware, and operational state
  tavily_search (if available) — search the web for current information
  handover(next_agent)     — transfer to a specialist agent

== GUIDELINES ==
- Keep replies short (1-3 sentences) unless the user needs detail.
- Use speak() to vocalize your response so the user hears you.
- If the user wants to order food, call handover("swiggy", reason="food order request").
- If the user asks about robot movement, call handover("navigate", reason="movement request").
- If the user asks what the robot sees, call handover("vision", reason="visual query").
- If the user asks about robot battery or hardware, call handover("status", reason="status query").
"""


def chat_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(CHAT_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "chat"}
