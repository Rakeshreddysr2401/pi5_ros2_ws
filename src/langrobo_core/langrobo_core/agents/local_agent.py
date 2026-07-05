"""Local multimodal agent — sees real camera frames and reasons over them.

Unlike the `vision` agent (which delegates to Jetson's Moondream and only gets
back text), local_agent runs on the local multimodal LLM (Gemma via llama.cpp)
and keeps the actual captured frames in its conversation. This makes follow-up
questions about the same scene ("did he wear spectacles?", "is this the real
poster?") reason over the real pixels instead of a one-shot text summary.

It is fed the IMAGE-PRESERVING projection of the shared log (keep_images=True),
and runs on a dedicated llama.cpp slot (see get_llm("local_agent")) so its
cached image prefix is not evicted by other agents' calls.

Prompt lives in langrobo_core/prompts.py (LOCAL_AGENT_PROMPT).
"""

import logging

from langchain_core.messages import SystemMessage

from ..services.llm import get_llm
from ..prompts import LOCAL_AGENT_PROMPT
from ..graph.state import AgentState
from ..tools import LOCAL_AGENT_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def build_llm_call(messages: list):
    """Return (llm, prompt_messages) for a local_agent turn.

    Shared by local_agent_node and agent_node's cache warmer — identical bound
    tools and prompt so the warmed slot-1 prefix (including camera frames)
    matches the next real request byte-for-byte.
    """
    llm = get_llm("local_agent").bind_tools(LOCAL_AGENT_TOOLS)
    clean = prepare_messages_for_agent(messages, keep_images=True)
    return llm, [SystemMessage(content=LOCAL_AGENT_PROMPT)] + clean


def local_agent_node(state: AgentState) -> dict:
    llm, msgs = build_llm_call(state["messages"])
    response = safe_invoke(llm, msgs, logger)
    return {"messages": [response], "active_agent": "local_agent"}
