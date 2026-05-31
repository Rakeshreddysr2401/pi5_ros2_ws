"""turn_entry — runs at the start of every user turn.

Resets per-turn loop guard counters and always routes to the supervisor
for fresh intent classification.
"""

from langgraph.types import Command

from ..state import AgentState


def turn_entry_node(state: AgentState) -> Command:
    return Command(
        goto="supervisor",
        update={"agent_turn_visits": {}, "always_speak": True},
    )
