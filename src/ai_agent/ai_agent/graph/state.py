from typing import Annotated, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    active_agent: str
    agent_turn_visits: dict
    always_speak: Optional[bool]
