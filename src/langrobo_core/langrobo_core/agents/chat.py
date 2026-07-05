"""Chat agent — general conversation, web search, reminders, system status, small talk.

Prompt lives in langrobo_core/prompts.py (CHAT_PROMPT); only the dynamic
context blocks (household memory, now-playing, watch state, today's date) are
assembled here, appended at the END so the static prefix stays KV-cached.
"""

import logging
from datetime import datetime

from langchain_core.messages import SystemMessage

from ..services.llm import get_llm
from ..prompts import CHAT_PROMPT
from ..graph.state import AgentState
from ..tools import CHAT_TOOLS
from ..tools.household import household_context
from ..tools.music import music_context
from ..tools.watch import watch_context
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def build_llm_call(messages: list):
    """Return (llm, prompt_messages) for a chat turn.

    Shared by chat_node and agent_node's cache warmer — the warmer must send the
    IDENTICAL bound tools and system prompt, otherwise it prefills a different
    formatted prompt and warms nothing (tool schemas are part of the template).
    """
    llm = get_llm("chat").bind_tools(CHAT_TOOLS)
    clean = prepare_messages_for_agent(messages)
    # Dynamic parts go at the END of the system prompt so the static prefix
    # stays reusable in the llama.cpp KV cache. Only the DATE is in-prompt
    # (changes once a day); the clock is a tool (get_current_time) — a
    # per-minute timestamp here made consecutive turns diverge mid-prompt and
    # re-prefill all ~2k tokens (~20s on the 12B Mac Mini) every minute tick.
    today = datetime.now().strftime("%A %B %d, %Y").replace(" 0", " ")
    prompt = (CHAT_PROMPT + household_context() + music_context() + watch_context()
              + f"\n== TODAY ==\nToday's date: {today}. For the clock time, call get_current_time.\n")
    return llm, [SystemMessage(content=prompt)] + clean


def chat_node(state: AgentState) -> dict:
    llm, msgs = build_llm_call(state["messages"])
    response = safe_invoke(llm, msgs, logger)
    return {"messages": [response], "active_agent": "chat"}
