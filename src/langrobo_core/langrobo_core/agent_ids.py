"""Agent names — the leaf module every other layer agrees with.

This exists as its own zero-import module for one reason: the handover tool
needs the list of legal routing targets to build its `next_agent` Literal (on
llama.cpp that Literal becomes the decoding grammar, so a small model
physically cannot emit a route to an agent that does not exist), and the tools
package cannot import the registry — the registry imports the tools.

Adding an agent starts here and continues in registry.py. Nothing else in the
codebase should hard-code an agent name.
"""

# THREE agents, and no router above them. Each owns one modality: text in/out
# (chat), images in (local_agent), motion out (navigate).
#
# There used to be a fourth, a `supervisor` whose only job was routing. It was
# removed on 2026-09-07 because it had stopped doing that job: agent_node
# enters every user turn at chat or the sticky agent, so the supervisor only
# ever saw [SYSTEM] turns — of which this build produces exactly one kind,
# navigation arrival, which always routes to chat. A whole agent, prompt and
# KV slot to make a decision with one possible answer. chat carries the
# routing table now, which it already did.
AGENT_IDS: tuple[str, ...] = (
    "chat",
    "local_agent",
    "navigate",
)

# Every name Command(goto=...) and handover(next_agent=...) may legally target.
# A handover to anything else is silently ignored by langgraph (unknown
# channel) and the turn would end with no reply at all.
ROUTABLE: tuple[str, ...] = AGENT_IDS
