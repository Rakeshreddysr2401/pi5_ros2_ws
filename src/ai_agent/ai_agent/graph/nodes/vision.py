"""Vision node — visual reasoning with camera tools."""

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..prompts import vision_prompt
from ..state import AgentState
from ..tools import vision_tools


def vision_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(vision_tools)
    response = llm.invoke([SystemMessage(content=vision_prompt)] + state["messages"])
    return {"messages": [response]}
