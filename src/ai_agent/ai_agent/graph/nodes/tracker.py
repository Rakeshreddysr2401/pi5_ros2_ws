"""Tracker agent — Swiggy delivery tracking + door navigation on arrival."""

import logging

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..persona import PERSONA
from ..state import AgentState
from ..tools import TRACKER_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = PERSONA + """\
Right now you track Swiggy deliveries. Your job is to check \
delivery status and act when the order arrives at the door.

Capabilities via tools:
- get_food_orders              : list recent orders
- get_food_order_details       : details for a specific order
- track_food_order             : live delivery tracking
- set_active_order(order_id)   : store/clear the order ID for background monitoring
- navigate_to(target)          : drive the robot to a location
- handover(next_agent, reason) : transfer to another agent

Guidelines:
- When chained right after an order is placed, immediately check status and report ETA.
- When a [SYSTEM] message reports the order as delivered:
    1. Call navigate_to("door") to drive the robot to the front door.
    2. Call set_active_order(None) to stop background polling.
    3. Put the announcement in your reply text (e.g. "Your order has arrived! I'm
       heading to the door to pick it up.") — it is spoken automatically.
    4. Call handover("chat", reason="order_picked_up", chain=True) so the robot \
can greet the delivery person or assist the user further.
- For status checks, report estimated delivery time, current status, and restaurant name.
- Once the tracking question is fully answered, call handover("supervisor", reason="tracking_done").
- For food ordering (not tracking), call handover("supervisor", reason="ordering_request").
- Put replies in your message text — it is spoken to the user automatically and is
  the ONLY thing said. Don't narrate tool use. NEVER hand over to "tracker" (yourself).
"""


def tracker_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(TRACKER_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "tracker"}
