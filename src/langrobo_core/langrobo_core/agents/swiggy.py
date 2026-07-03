"""Swiggy agent — food ordering, restaurant search, cart management."""

import logging

from langchain_core.messages import SystemMessage

from ..services.llm import get_llm
from .persona import PERSONA
from ..graph.state import AgentState
from ..tools import SWIGGY_TOOLS
from ..tools.swiggy_mcp import SWIGGY_FOOD_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

# When the Swiggy MCP server is unreachable, SWIGGY_FOOD_TOOLS is empty (search,
# menu, cart, order tools are all missing). Without this note the model flails with
# the only tools it has left and loops until the graph loop guard ends the turn.
_FOOD_UNAVAILABLE_NOTE = """

IMPORTANT: Food ordering is temporarily unavailable — the Swiggy service is not \
reachable right now, so you CANNOT search restaurants, browse menus, or place \
orders. Do not call any tools. Simply tell the user that food ordering is \
temporarily unavailable and to try again later, then \
handover("supervisor", reason="swiggy_unavailable")."""

_PROMPT = PERSONA + """\
Right now you handle Swiggy food ordering. \
Help users discover restaurants, browse menus, manage their cart, and place delivery orders.

Capabilities via tools:
- Search restaurants and dishes by cuisine, location, or name
- Browse restaurant menus with variants and add-ons
- Get saved delivery addresses
- Manage cart: view, add/modify items, apply coupons
- Place orders

Guidelines:
- Always confirm delivery address before placing an order.
- Ask for clarification on item variants (size, spice level, add-ons) when relevant.
- Show a cart summary before placing and require explicit user confirmation ("yes", "confirm").
- Never place an order without explicit user confirmation.
- After successfully placing an order, call set_active_order(order_id) with the order ID so \
the robot monitors delivery, then respond with a confirmation message and call:
    handover("tracker", reason="order_placed", chain=True)
  so the tracker immediately follows the delivery.
- You cannot see camera images. If the order depends on something the robot SAW
  (a routing note like "order what's on the shelf") and a needed detail is missing
  or ambiguous, call handover("local_agent", reason="look and answer: <specific
  question>") — it will look and hand back with the answer. Ask the USER only for
  choices that are theirs (variant, quantity, address), not for what is visible.
- For non-food questions call handover("supervisor", reason="not food related").
- Put replies in your message text — it is spoken to the user automatically and is
  the ONLY thing said. Don't narrate tool use; just do the task and reply. NEVER hand
  over to "swiggy" (yourself) — do the task, then hand over as described above.
"""


def swiggy_node(state: AgentState) -> dict:
    llm = get_llm("swiggy").bind_tools(SWIGGY_TOOLS)
    prompt = _PROMPT if SWIGGY_FOOD_TOOLS else _PROMPT + _FOOD_UNAVAILABLE_NOTE
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=prompt)] + clean, logger)
    return {"messages": [response], "active_agent": "swiggy"}
