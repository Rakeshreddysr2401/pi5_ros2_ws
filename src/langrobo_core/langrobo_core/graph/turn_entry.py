"""turn_entry — runs at the start of every user turn.

Resets per-turn loop-guard counters, then routes:
  - to a *sticky* specialist if the previous turn left one active (skips any
    routing LLM hop for in-domain follow-ups),
  - to the supervisor only when agent_node forced it ([SYSTEM] events), or
  - to `chat` otherwise — chat is the default responder AND carries the full
    routing table, so the common case (fresh general turn) costs ONE LLM call
    instead of the serial supervisor→agent pair.

Which agents are sticky is declared per-agent in registry.py (`sticky=True`),
not listed here. They are the agents that answer in plain text (no mandatory
hand-back to supervisor) AND can re-route a topic change themselves: `chat`
carries the full routing table, and `local_agent` owns visual follow-ups
("what colour is it?") with an out-of-scope catch-all. `navigate` hands back
to the supervisor by design, so it is intentionally NOT sticky — the turn
after it starts at chat.

agent_node passes the previous turn's active_agent in as the incoming state, and
forces "supervisor" for [SYSTEM] events so those always get a fresh route.
"""

from typing import Literal

from langgraph.types import Command

from ..agent_ids import ROUTABLE
from ..registry import STICKY_ELIGIBLE
from .state import AgentState


def turn_entry_node(state: AgentState) -> Command[Literal[ROUTABLE]]:  # type: ignore[valid-type]
    incoming = state.get("active_agent")
    if incoming == "supervisor":
        target = "supervisor"        # forced by agent_node for [SYSTEM] events
    elif incoming in STICKY_ELIGIBLE:
        target = incoming
    else:
        target = "chat"
    return Command(
        goto=target,
        update={"agent_turn_visits": {}, "agent_run_counts": {}, "always_speak": True},
    )
