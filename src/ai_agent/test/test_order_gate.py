"""Unit test for the structural order-confirmation gate (graph zone, no ROS2).

Tests the pure decision (partition_order_calls) — the part that guarantees an order
can never be placed in a single shot. The ToolNode execution it forwards to is
standard and exercised by the real graph build, not re-tested here.

Run: ./.venv/bin/python src/ai_agent/test/test_order_gate.py
"""
import os
import sys

os.environ.setdefault("SWIGGY_ENABLED", "0")  # no MCP network during the test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_agent.graph.nodes.swiggy_tools import partition_order_calls  # noqa: E402


class FakeBridge:
    def __init__(self):
        self._turn = 0
        self._arm = None

    def bump_turn(self):
        self._turn += 1

    def arm_order(self, key):
        self._arm = (key, self._turn)

    def order_confirmed(self, key):
        return bool(self._arm) and self._arm[0] == key and self._turn > self._arm[1]

    def clear_order_arm(self):
        self._arm = None


def _call(name, args, cid="c1"):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


def run():
    b = FakeBridge()

    # 1) Non-order call always allowed.
    blocked, allowed = partition_order_calls([_call("search_restaurants", {"q": "pizza"})], b)
    assert blocked == [] and len(allowed) == 1

    # 2) First order call in a turn -> blocked + armed, not allowed.
    b.bump_turn()  # turn 1: user asks to order
    blocked, allowed = partition_order_calls([_call("place_cart_order", {"cart_id": "X"})], b)
    assert len(blocked) == 1 and allowed == []
    assert "CONFIRMATION_REQUIRED" in blocked[0].content

    # 3) Re-call in the SAME turn -> still blocked (no human spoke between).
    blocked, allowed = partition_order_calls([_call("place_cart_order", {"cart_id": "X"})], b)
    assert len(blocked) == 1 and allowed == []

    # 4) User speaks again (turn advances), same args -> NOW allowed through.
    b.bump_turn()  # turn 2: user confirms
    blocked, allowed = partition_order_calls([_call("place_cart_order", {"cart_id": "X"})], b)
    assert blocked == [] and len(allowed) == 1, "should execute after confirmation on a later turn"

    # 5) A different order re-arms (fail closed), does not ride the prior confirmation.
    b.bump_turn()  # turn 3
    blocked, allowed = partition_order_calls([_call("place_cart_order", {"cart_id": "Y"})], b)
    assert len(blocked) == 1 and allowed == [], "new order must re-confirm"

    # 6) Mixed batch: order blocked, non-order still allowed in the same response.
    b.bump_turn()  # turn 4
    blocked, allowed = partition_order_calls(
        [_call("place_cart_order", {"cart_id": "Z"}, "o1"), _call("speak", {"text": "ok"}, "s1")], b)
    assert len(blocked) == 1 and len(allowed) == 1
    assert allowed[0]["name"] == "speak"

    print("OK: order gate holds - single-shot blocked, placement requires a later user turn")


if __name__ == "__main__":
    run()
