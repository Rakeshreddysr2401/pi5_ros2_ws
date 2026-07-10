"""Instamart agent — grocery/essentials search, cart, quick-commerce orders.

Prompt lives in langrobo_core/prompts.py (INSTAMART_PROMPT +
INSTAMART_UNAVAILABLE_NOTE for the tokenless/unreachable/stale-login path).
"""

import logging

from langchain_core.messages import SystemMessage

from ..services import mcp
from ..services.llm import get_llm
from ..prompts import INSTAMART_PROMPT, INSTAMART_UNAVAILABLE_NOTE
from ..graph.state import AgentState
from ..tools import INSTAMART_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def instamart_node(state: AgentState) -> dict:
    mcp.refresh_tokens_if_changed()  # one stat; re-arms after a re-login
    llm = get_llm("instamart").bind_tools(INSTAMART_TOOLS)
    prompt = (INSTAMART_PROMPT if mcp.provider_ok("swiggy_instamart")
              else INSTAMART_PROMPT + INSTAMART_UNAVAILABLE_NOTE)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=prompt)] + clean, logger)
    return {"messages": [response], "active_agent": "instamart"}
