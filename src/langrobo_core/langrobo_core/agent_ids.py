"""Agent names — the leaf module every other layer agrees with.

This exists as its own zero-import module for one reason: the handover tool
needs the list of legal routing targets to build its `next_agent` Literal (on
llama.cpp that Literal becomes the decoding grammar, so a small model
physically cannot emit a route to an agent that does not exist), and the tools
package cannot import the registry — the registry imports the tools.

Adding an agent starts here and continues in registry.py. Nothing else in the
codebase should hard-code an agent name.
"""

# Routing targets, in the order the supervisor sees them. `supervisor` is not
# here: it routes, it is not a destination the registry describes.
AGENT_IDS: tuple[str, ...] = (
    "chat",
    "local_agent",
    "navigate",
    "status",
    "swiggy",
    "instamart",
    "dineout",
    "tracker",
    "knowledge",
    "briefing",
)

# Every name Command(goto=...) and handover(next_agent=...) may legally target.
# A handover to anything else is silently ignored by langgraph (unknown
# channel) and the turn would end with no reply at all.
ROUTABLE: tuple[str, ...] = ("supervisor",) + AGENT_IDS
