"""Navigate agent — map-based and object-based navigation.

Prompt lives in langrobo_core/prompts.py (NAVIGATE_PROMPT). Person following
("follow me" / "come to me") is deliberately declined there until the depth
camera lands — the 2D servo would drive blind at a moving person.
"""

import logging

from langchain_core.messages import SystemMessage

from ..services.llm import get_llm
from ..prompts import NAVIGATE_PROMPT
from ..graph.state import AgentState
from ..tools import NAVIGATE_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def navigate_node(state: AgentState) -> dict:
    llm = get_llm("navigate").bind_tools(NAVIGATE_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=NAVIGATE_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "navigate"}
