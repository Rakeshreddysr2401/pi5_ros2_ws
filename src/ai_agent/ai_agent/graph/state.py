from typing import Annotated, Literal, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    # Full conversation — managed by LangGraph's add_messages reducer
    messages: Annotated[list, add_messages]
    # Set by router_node so tool_node knows where to return after tool calls
    intent: Optional[Literal["chat", "vision", "navigate", "status"]]
