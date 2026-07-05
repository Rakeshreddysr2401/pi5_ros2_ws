"""Briefing agent — the scheduled morning summary + on-demand briefings.

The household lists/facts ride in the prompt tail (same dynamic block as
chat) so the briefing reads them without tool calls; reminders and weather
come from tools. Prompt lives in langrobo_core/prompts.py (BRIEFING_PROMPT).
"""

import logging
from datetime import datetime

from langchain_core.messages import SystemMessage

from ..services.llm import get_llm
from ..prompts import BRIEFING_PROMPT
from ..graph.state import AgentState
from ..tools import BRIEFING_TOOLS
from ..tools.household import household_context
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def briefing_node(state: AgentState) -> dict:
    llm = get_llm("briefing").bind_tools(BRIEFING_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    today = datetime.now().strftime("%A %B %d, %Y").replace(" 0", " ")
    prompt = (BRIEFING_PROMPT + household_context()
              + f"\n== TODAY ==\nToday's date: {today}.\n")
    response = safe_invoke(llm, [SystemMessage(content=prompt)] + clean, logger)
    return {"messages": [response], "active_agent": "briefing"}
