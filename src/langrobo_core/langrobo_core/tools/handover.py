"""The handover tool — the only way control moves between agents.

`next_agent` is a Literal built from agent_ids.ROUTABLE rather than a
hand-written list. That matters beyond tidiness: with strict_tool_calls on,
llama.cpp turns this schema into a decoding grammar, so the enum IS the set of
routes a small model can physically emit. A hand-maintained copy that fell
behind the registry meant either a dead route in the grammar or a live agent
the supervisor could never reach.
"""

import json
from typing import Literal

from langchain_core.tools import tool

from ..agent_ids import ROUTABLE

HANDOVER_NAMES = {"handover"}

# Literal[] accepts a tuple at runtime, so the enum tracks the registry.
_NextAgent = Literal[ROUTABLE]           # type: ignore[valid-type]


@tool("handover")
def handover(
    next_agent: _NextAgent,
    reason: str = "",
    chain: bool = False,
) -> str:
    """Transfer the conversation to another agent.

    Args:
        next_agent: Agent to route to.
        reason: Why this handover is happening (e.g. "order_placed", "cannot_answer").
        chain: True -> next agent responds immediately in the same turn.
               Use when the next agent must ACT on this one's result (e.g.
               local_agent identifies an object, then navigate drives to it).
               False -> this agent has already answered; the turn ends here.
    """
    return json.dumps({"next_agent": next_agent, "reason": reason, "chain": chain})


handover.description = handover.description.replace(
    "Agent to route to.", "Agent to route to — " + " | ".join(ROUTABLE) + "."
)
