"""turn_entry — runs at the start of every user turn.

Resets per-turn loop-guard counters, then routes:
  - to a *sticky* agent if the previous turn left one active (skips the
    routing hop entirely for in-domain follow-ups), or
  - to `chat` otherwise — chat is the default responder AND carries the full
    routing table, so the common case costs ONE LLM call.

There is no separate router node to enter. agent_node also enters `local_agent`
directly, with the camera frame already attached, for an utterance
fastpath.is_vision_question() is certain about.

Which agents are sticky is declared per-agent in registry.py (`sticky=True`),
not listed here. They are the agents that answer in plain text (no mandatory
mandatory hand-back) AND can re-route a topic change themselves: `chat`
carries the full routing table, and `local_agent` owns visual follow-ups
("what colour is it?") with an out-of-scope catch-all. `navigate` ends its
turn with a plain confirmation and no routing opinion, so it is intentionally
NOT sticky — the turn after it starts at chat.

agent_node passes the previous turn's active_agent in as the incoming state.
"""

from typing import Literal

from langgraph.types import Command

from ..agent_ids import ROUTABLE
from ..registry import STICKY_ELIGIBLE
from .state import AgentState


def turn_entry_node(state: AgentState) -> Command[Literal[ROUTABLE]]:  # type: ignore[valid-type]
    incoming = state.get("active_agent")
    target = incoming if incoming in STICKY_ELIGIBLE else "chat"
    return Command(
        goto=target,
        update={"agent_turn_visits": {}, "agent_run_counts": {}, "always_speak": True},
    )
