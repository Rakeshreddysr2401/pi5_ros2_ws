"""Supervisor — pure router. Always calls handover(), never responds to the user."""

import logging

from langchain_core.messages import AIMessage, SystemMessage

from ..llm import get_llm
from ..state import AgentState
from ..tools.handover import handover, HANDOVER_NAMES
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke
from ._registry import build_supervisor_agent_list

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are a routing supervisor for a home robot. Your ONLY job is to decide which \
agent should handle the user's request and call handover() immediately. \
You NEVER respond to the user with text.

Available agents:
{agent_list}

Rules:
1. Always call handover() — never write a text response.
2. Pass a short reason (e.g. "user wants to order food", "user asking about delivery").
3. When unsure between chat and another agent, prefer the more specific agent.
4. When in doubt or the request is ambiguous, route to "chat".
"""


def _get_prompt() -> str:
    return _PROMPT_TEMPLATE.format(agent_list=build_supervisor_agent_list())


def supervisor_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools([handover])
    clean = prepare_messages_for_agent(state["messages"], keep_all_system_msgs=True)
    response = safe_invoke(llm, [SystemMessage(content=_get_prompt())] + clean, logger)
    # Strip any stray text — supervisor must stay silent
    if response.tool_calls and any(tc["name"] in HANDOVER_NAMES for tc in response.tool_calls):
        response = AIMessage(content="", tool_calls=response.tool_calls, id=response.id)
    return {"messages": [response], "active_agent": "supervisor"}
