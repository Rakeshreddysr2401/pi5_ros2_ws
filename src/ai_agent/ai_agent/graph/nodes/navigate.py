"""Navigate node — movement planning and autonomous navigation."""

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..prompts import navigator_prompt
from ..state import AgentState
from ..tools import navigator_tools


def navigate_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(navigator_tools)
    response = llm.invoke([SystemMessage(content=navigator_prompt)] + state["messages"])
    return {"messages": [response]}
