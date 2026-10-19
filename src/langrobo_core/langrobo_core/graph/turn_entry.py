"""turn_entry — runs at the start of every user turn.

Resets per-turn loop-guard counters, then routes:
  - to a *sticky* agent if the previous turn left one active (skips the
    routing hop entirely for in-domain follow-ups), or
  - to `chat` otherwise — chat is the default responder AND carries the full
    routing table, so the common case costs ONE LLM call.

There is no separate router node to enter, and no shortcut around this one:
the regex fast path that used to pre-empt it was removed (ARCHITECTURE_LLD.md
§3.1), so every user turn arrives here.

Which agents are sticky is declared per-agent in registry.py (`sticky=True`),
not listed here. The requirement is that the agent can re-route a topic change
itself, because a sticky agent sees follow-ups that may not be its own:
`chat` carries the full routing table, `local_agent` owns visual follow-ups
("what colour is it?") with an out-of-scope catch-all, and `navigate` owns
multi-step drives ("now turn left") with the same catch-all in rule 6 of its
prompt. All three are sticky as of 2026-09-08.

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
