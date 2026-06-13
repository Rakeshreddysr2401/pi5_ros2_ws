from typing import Optional

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    active_agent: str
    agent_turn_visits: dict
    always_speak: Optional[bool]

