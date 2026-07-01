from typing import Optional

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    active_agent: str
    agent_turn_visits: dict
    # How many times each agent NODE has executed this turn. Guards against an
    # agent looping on its own (non-handover) tools forever — that path never
    # reaches handle_handover, so agent_turn_visits alone can't catch it.
    agent_run_counts: dict
    always_speak: Optional[bool]

