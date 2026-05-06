"""Status node — robot operational state queries."""

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..prompts import status_prompt
from ..state import AgentState
from ..tools import status_tools


def status_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(status_tools)
    response = llm.invoke([SystemMessage(content=status_prompt)] + state["messages"])
    return {"messages": [response]}
