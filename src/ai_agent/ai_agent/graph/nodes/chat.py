"""Chat node — general conversation, no tools."""

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..prompts import chat_prompt
from ..state import AgentState


def chat_node(state: AgentState) -> dict:
    llm = get_llm()
    response = llm.invoke([SystemMessage(content=chat_prompt)] + state["messages"])
    return {"messages": [response]}
