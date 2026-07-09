"""Supervisor — pure router. Always calls handover(), never responds to the user.

Prompt lives in langrobo_core/prompts.py (SUPERVISOR_PROMPT_TEMPLATE); the
agent list is filled in from graph/registry.py at call time.
"""

import logging

from langchain_core.messages import AIMessage, SystemMessage

from ..services.llm import get_llm, strict_tools_enabled
from ..prompts import SUPERVISOR_PROMPT_TEMPLATE
from ..graph.state import AgentState
from ..tools.handover import handover, HANDOVER_NAMES
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke
from ..graph.registry import build_supervisor_agent_list

logger = logging.getLogger(__name__)


def _get_prompt() -> str:
    return SUPERVISOR_PROMPT_TEMPLATE.format(agent_list=build_supervisor_agent_list())


def build_llm_call(messages: list):
    """Return (llm, prompt_messages) for a supervisor turn.

    Shared by supervisor_node and agent_node's cache warmer — same contract as
    chat.py's build_llm_call. The warmer must send the IDENTICAL bound tools,
    tool_choice, and system prompt or it prefills a differently-shaped prompt
    and warms nothing on the supervisor's pinned slot.
    """
    # The supervisor MUST emit exactly one handover and never free text. Forcing
    # tool_choice makes that structural: on llama.cpp the server grammar-constrains
    # the output to a valid handover (incl. the next_agent enum) generated from the
    # tool schema; OpenAI honors the same field. Falls back to plain bind_tools if
    # disabled (e.g. a llama.cpp build without --jinja tool support).
    if strict_tools_enabled():
        llm = get_llm("supervisor").bind_tools([handover], tool_choice="handover")
    else:
        llm = get_llm("supervisor").bind_tools([handover])
    clean = prepare_messages_for_agent(messages)
    return llm, [SystemMessage(content=_get_prompt())] + clean


def supervisor_node(state: AgentState) -> dict:
    llm, msgs = build_llm_call(state["messages"])
    response = safe_invoke(llm, msgs, logger, agent="supervisor")
    # Strip stray text and deduplicate — supervisor emits exactly one handover call
    if response.tool_calls and any(tc["name"] in HANDOVER_NAMES for tc in response.tool_calls):
        first = next(tc for tc in response.tool_calls if tc["name"] in HANDOVER_NAMES)
        response = AIMessage(content="", tool_calls=[first], id=response.id)
    return {"messages": [response], "active_agent": "supervisor"}
