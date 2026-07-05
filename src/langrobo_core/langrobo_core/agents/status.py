"""Status agent — robot operational state, battery, hardware queries.

Prompt lives in langrobo_core/prompts.py (STATUS_PROMPT).
"""

import logging

from langchain_core.messages import SystemMessage

from ..services.llm import get_llm
from ..prompts import STATUS_PROMPT
from ..graph.state import AgentState
from ..tools import STATUS_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def status_node(state: AgentState) -> dict:
    llm = get_llm("status").bind_tools(STATUS_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=STATUS_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "status"}
