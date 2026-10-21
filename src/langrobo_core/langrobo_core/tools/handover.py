"""The handover tool — the only way control moves between agents.

`next_agent` is a Literal built from agent_ids.ROUTABLE rather than a
hand-written list. That matters beyond tidiness: llama.cpp turns this schema
into a decoding grammar, so the enum IS the set of routes a small model can
physically emit. A hand-maintained copy that fell behind the registry meant
either a dead route in the grammar or a live agent nothing could reach.
"""

import json
from typing import Literal

from langchain_core.tools import tool

from ..agent_ids import ROUTABLE

HANDOVER_NAMES = {"handover"}

# Literal[] accepts a tuple at runtime, so the enum tracks the registry.
_NextAgent = Literal[ROUTABLE]           # type: ignore[valid-type]


@tool("handover")
def handover(next_agent: _NextAgent, reason: str = "") -> str:
    """Pass this turn to another agent; it answers the user in this same turn.

    Say nothing yourself when you call it.

    Args:
        next_agent: Agent to route to.
        reason: What the next agent must do, in one short phrase -- it sees this
            and the user's words, nothing you saw (e.g. "go near the red bag on the sofa").
    """
    # Always hands the turn on (2026-10-21): an old `chain=False` ended the turn
    # with only the caller's words -- "Let me check." and nothing done.
    return json.dumps({"next_agent": next_agent, "reason": reason})


handover.description = handover.description.replace(
    "Agent to route to.", "Agent to route to — " + " | ".join(ROUTABLE) + "."
)
