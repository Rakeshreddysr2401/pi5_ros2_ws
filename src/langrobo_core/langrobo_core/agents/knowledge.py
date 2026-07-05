"""Knowledge agent — Q&A over the household's ingested documents.

Ported from the SubAgents predecessor's RAG module, adapted to the embedded
Qdrant + fastembed stack (services/knowledge.py, tools/knowledge.py).
Prompt lives in langrobo_core/prompts.py (KNOWLEDGE_PROMPT).
"""

import logging

from langchain_core.messages import SystemMessage

from ..services.llm import get_llm
from ..prompts import KNOWLEDGE_PROMPT
from ..graph.state import AgentState
from ..tools import KNOWLEDGE_AGENT_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def knowledge_node(state: AgentState) -> dict:
    llm = get_llm("knowledge").bind_tools(KNOWLEDGE_AGENT_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=KNOWLEDGE_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "knowledge"}
