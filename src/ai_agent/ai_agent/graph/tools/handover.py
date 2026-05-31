import json
from typing import Literal

from langchain_core.tools import tool

HANDOVER_NAMES = {"handover"}


@tool("handover")
def handover(
    next_agent: Literal["supervisor", "chat", "vision", "navigate", "status", "swiggy", "tracker"],
    reason: str = "",
    chain: bool = False,
) -> str:
    """Transfer the conversation to another agent.

    Args:
        next_agent: Agent to route to — supervisor | chat | vision | navigate | status | swiggy | tracker
        reason: Why this handover is happening (e.g. "order_placed", "cannot_answer").
        chain: True → next agent responds immediately in the same turn.
               Use for swiggy → tracker after placing an order.
    """
    return json.dumps({"next_agent": next_agent, "reason": reason, "chain": chain})
