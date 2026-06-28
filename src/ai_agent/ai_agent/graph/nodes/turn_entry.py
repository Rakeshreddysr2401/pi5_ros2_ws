"""turn_entry — runs at the start of every user turn.

Resets per-turn loop-guard counters, then routes:
  - to a *sticky* specialist if the previous turn left one active (skips the
    supervisor LLM hop for in-domain follow-ups), or
  - to the supervisor for fresh intent classification otherwise.

Only agents in STICKY_ELIGIBLE may be entered directly. They are the agents that
answer in plain text (no mandatory hand-back to supervisor) AND can re-route a
topic change themselves: `chat` carries the full routing table, and `local_agent`
owns visual follow-ups ("what colour is it?") with an out-of-scope catch-all.
Specialists like navigate/swiggy/status hand back to supervisor by design, so
they are intentionally NOT sticky.

agent_node passes the previous turn's active_agent in as the incoming state, and
forces "supervisor" for [SYSTEM] events so those always get a fresh route.
"""

from langgraph.types import Command

from ..state import AgentState

STICKY_ELIGIBLE = {"chat", "local_agent"}


def turn_entry_node(state: AgentState) -> Command:
    incoming = state.get("active_agent")
    target = incoming if incoming in STICKY_ELIGIBLE else "supervisor"
    return Command(
        goto=target,
        update={"agent_turn_visits": {}, "always_speak": True},
    )
