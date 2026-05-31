"""Supervisor — pure router. Always calls handover(), never responds to the user."""

import logging

from langchain_core.messages import AIMessage, SystemMessage

from ..llm import get_llm
from ..state import AgentState
from ..tools.handover import handover, HANDOVER_NAMES
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = """\
You are a routing supervisor for a home robot. Your ONLY job is to decide which \
agent should handle the user's request and call handover() immediately. \
You NEVER respond to the user with text.

Available agents:
- "chat"     : general questions, web search, system status, small talk, anything not in the others
- "vision"   : what the robot sees, object detection, scene description, visual queries
- "navigate" : moving the robot, going somewhere, finding and approaching objects, stop/follow
- "status"   : robot battery, hardware state, what the robot is currently doing
- "swiggy"   : food ordering, restaurant search, browsing menus, managing cart, placing orders
- "tracker"  : checking delivery status, order ETA, tracking a Swiggy order

Rules:
1. Always call handover() — never write a text response.
2. Pass a short reason (e.g. "user wants to order food", "user asking about delivery").
3. When unsure between chat and another agent, prefer the more specific agent.
"""


def supervisor_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools([handover])
    clean = prepare_messages_for_agent(state["messages"], keep_all_system_msgs=True)
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    # Strip any stray text — supervisor must stay silent
    if response.tool_calls and any(tc["name"] in HANDOVER_NAMES for tc in response.tool_calls):
        response = AIMessage(content="", tool_calls=response.tool_calls, id=response.id)
    return {"messages": [response], "active_agent": "supervisor"}
