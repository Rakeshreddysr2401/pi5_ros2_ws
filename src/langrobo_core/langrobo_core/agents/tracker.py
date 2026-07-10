"""Tracker agent — Swiggy food/Instamart delivery tracking + door navigation on arrival.

Prompt lives in langrobo_core/prompts.py (TRACKER_PROMPT).
"""

import logging

from langchain_core.messages import SystemMessage

from ..services import mcp
from ..services.llm import get_llm
from ..prompts import TRACKER_PROMPT
from ..graph.state import AgentState
from ..tools import TRACKER_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def tracker_node(state: AgentState) -> dict:
    mcp.refresh_tokens_if_changed()  # one stat; re-arms after a re-login
    llm = get_llm("tracker").bind_tools(TRACKER_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=TRACKER_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "tracker"}
