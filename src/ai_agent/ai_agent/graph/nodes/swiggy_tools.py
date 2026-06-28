"""Swiggy tool node with a structural order-confirmation gate.

The real "place order" action is an external Swiggy MCP tool we do not control,
so we gate it at the tool-node level instead of wrapping the opaque tool:

  1. The swiggy agent calls an order tool (name matches an ORDER pattern).
  2. First time: we DON'T execute it. We arm the bridge and return a
     CONFIRMATION_REQUIRED ToolMessage telling the model to summarise the order,
     ask the user, and end the turn.
  3. Only after the user speaks again (the bridge turn counter advances) does a
     re-call with the SAME arguments actually execute the order tool.

This is a checkpointer-free human-in-the-loop: an order can never be placed in a
single shot — the human must speak between the request and the placement. Non-order
tool calls (search, cart, handover, speak) always pass straight through.

`make_swiggy_tools_node(tools)` is a factory so tests can inject fake tools.
"""

import json

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode

from ..tools import _bridge

# Substrings that mark an irreversible ordering / payment action. Matched against
# the MCP tool name (case-insensitive) so it works whatever Swiggy names them.
_ORDER_PATTERNS = (
    "place_order", "place_cart_order", "create_order",
    "checkout", "confirm_order", "pay",
)

_CONFIRM_MSG = (
    "CONFIRMATION_REQUIRED: Do NOT place this order yet. Tell the user the exact "
    "order summary (items and total and delivery address) and ask them to confirm "
    "out loud. End your turn now. Only AFTER the user confirms in a new reply may "
    "you call this tool again with the same arguments to actually place the order."
)


def _is_order_tool(name: str) -> bool:
    n = (name or "").lower()
    return any(p in n for p in _ORDER_PATTERNS)


def _order_key(call: dict) -> str:
    try:
        args = json.dumps(call.get("args", {}), sort_keys=True)
    except (TypeError, ValueError):
        args = str(call.get("args"))
    return f"{call['name']}::{args}"


def partition_order_calls(calls: list, bridge):
    """Pure decision: split tool calls into (blocked ToolMessages, allowed calls).

    An order call is allowed through ONLY if it was armed on an earlier turn
    (the user has spoken since). Otherwise it is blocked with a CONFIRMATION_REQUIRED
    message and (re)armed. Non-order calls always pass through. Side effects are
    limited to bridge.arm_order/clear_order_arm so the structural guarantee — no
    single-shot order placement — is fully unit-testable without the ToolNode.
    """
    blocked_msgs = []
    allowed_calls = []
    for c in calls:
        if _is_order_tool(c["name"]):
            key = _order_key(c)
            if bridge.order_confirmed(key):
                bridge.clear_order_arm()
                allowed_calls.append(c)              # confirmed → execute for real
            else:
                bridge.arm_order(key)                # arm; require a later user turn
                blocked_msgs.append(ToolMessage(
                    content=_CONFIRM_MSG, name=c["name"], tool_call_id=c["id"]))
        else:
            allowed_calls.append(c)                  # non-order (handover/speak/…) → run
    return blocked_msgs, allowed_calls


def make_swiggy_tools_node(tools: list):
    """Build a tool node for swiggy that gates order-placement tools."""
    base_node = ToolNode(tools)

    def swiggy_tools_node(state, config=None):
        bridge = _bridge.get()
        msgs = state["messages"]
        last = msgs[-1] if msgs else None
        calls = list(getattr(last, "tool_calls", None) or [])

        # Fast path: nothing to gate.
        if not calls or not any(_is_order_tool(c["name"]) for c in calls):
            return base_node.invoke(state, config)

        blocked_msgs, allowed_calls = partition_order_calls(calls, bridge)

        out_messages = list(blocked_msgs)
        if allowed_calls:
            gated = AIMessage(
                content=getattr(last, "content", "") or "",
                tool_calls=allowed_calls,
                id=getattr(last, "id", None),
            )
            sub = base_node.invoke({**state, "messages": msgs[:-1] + [gated]}, config)
            out_messages += sub["messages"]

        return {"messages": out_messages}

    return swiggy_tools_node
