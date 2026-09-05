"""Agent registry — one AgentSpec per agent, and nothing about an agent lives
anywhere else.

Before this file, adding an agent meant editing six places that had no way to
stay in sync: registry (description/examples), build.py's `_AGENT_SPECS`
(node + tools), tools/__init__.py (the tool set), prompts.py (the prompt),
turn_entry.py (`STICKY_ELIGIBLE`) and handover.py (the routing Literal). Drift
was invisible until the robot misbehaved — the tracker prompt spent months
telling the model to call a `navigate_to` tool that does not exist.

Now: add the name to agent_ids.py, add one AgentSpec here, and the graph, the
handover grammar, the supervisor's routing table, the sticky-entry set and the
agent's rendered tool block all follow. `tests/test_smoke.py` fails if they
ever diverge again.

The prompt in a spec is a TEMPLATE: its `{tools}` placeholder is filled from
`spec.tools` at import time (see agents/factory.py), so a prompt cannot name a
tool the agent is not bound to. Substitution happens once per process, which
keeps the KV-cache prefix rule in prompts.py intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from . import prompts
from .agent_ids import AGENT_IDS, ROUTABLE
from .tools import (
    BRIEFING_TOOLS,
    CHAT_TOOLS,
    DINEOUT_TOOLS,
    INSTAMART_TOOLS,
    KNOWLEDGE_AGENT_TOOLS,
    LOCAL_AGENT_TOOLS,
    NAVIGATE_TOOLS,
    STATUS_TOOLS,
    SUPERVISOR_TOOLS,
    SWIGGY_TOOLS,
    TRACKER_TOOLS,
)
from .tools.household import household_context
from .tools.music import music_context
from .tools.watch import watch_context


# ── Dynamic prompt tails ─────────────────────────────────────────────────────
# Appended at the END of the system prompt, never the middle: the static prefix
# in front of them is what stays resident in the agent's llama.cpp KV slot.
# Only the DATE goes in — the clock is the get_current_time tool, because a
# per-minute timestamp made consecutive turns diverge mid-prompt and re-prefill
# every ~2k tokens on each minute tick (~20s on the 12B model).

def _today_line(clock_hint: bool = True) -> str:
    today = datetime.now().strftime("%A %B %d, %Y").replace(" 0", " ")
    tail = " For the clock time, call get_current_time." if clock_hint else ""
    return f"\n== TODAY ==\nToday's date: {today}.{tail}\n"


def chat_context() -> str:
    return household_context() + music_context() + watch_context() + _today_line()


def briefing_context() -> str:
    return household_context() + _today_line(clock_hint=False)


# ── The spec ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentSpec:
    """Everything the system needs to know about one agent.

    description / examples  routing copy — feeds the supervisor's agent list and
                            the handover routing notes.
    prompt                  system-prompt template containing `{tools}`.
    tools                   the bound tool set; also what fills `{tools}`.
    sticky                  may a follow-up turn re-enter this agent directly,
                            skipping the routing hop? Only for agents that
                            answer in plain text AND can re-route a topic change
                            themselves (see graph/turn_entry.py).
    keep_images             feed this agent the image-preserving projection of
                            the shared log (multimodal agents only).
    context                 optional callable returning the dynamic prompt tail.
    mcp_provider            services/mcp.py provider this agent OWNS; when it is
                            down the agent's prompt gains `unavailable_note`.
    refresh_mcp             re-stat the token file on entry without owning a
                            provider — tracker reads two providers' tools and
                            owns neither, so it needs the refresh but must not
                            claim either one is unavailable.
    """

    name: str
    description: str
    examples: list[str]
    prompt: str
    tools: list
    sticky: bool = False
    keep_images: bool = False
    context: Optional[Callable[[], str]] = None
    mcp_provider: Optional[str] = None
    refresh_mcp: bool = False
    unavailable_note: str = ""
    routable: bool = True


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="supervisor",
        description="",           # the supervisor is not a routing destination
        examples=[],
        prompt=prompts.SUPERVISOR_PROMPT_TEMPLATE,
        tools=SUPERVISOR_TOOLS,
        routable=False,
    ),
    AgentSpec(
        name="chat",
        description="general questions, web search, reminders and timers (setting, listing, announcing due ones), home watch mode (arm/disarm, announcing alerts), system status, small talk, anything not covered by other agents",
        examples=["what's the weather?", "tell me a joke", "remind me in 10 minutes", "watch the house", "[SYSTEM] Reminder due", "[SYSTEM] Watch alert", "[SYSTEM] … announce this aloud …"],
        prompt=prompts.CHAT_PROMPT,
        tools=CHAT_TOOLS,
        sticky=True,
        context=chat_context,
    ),
    AgentSpec(
        name="local_agent",
        description="anything about what the robot sees — scene description, object/person detection, visual queries, and follow-up questions about the same scene (reasons over the actual camera image and remembers it)",
        examples=["what do you see?", "is there anyone in the room?", "did he wear spectacles?", "is this the real poster?"],
        prompt=prompts.LOCAL_AGENT_PROMPT,
        tools=LOCAL_AGENT_TOOLS,
        sticky=True,
        keep_images=True,
    ),
    AgentSpec(
        name="navigate",
        description="moving the robot, going somewhere, finding and approaching people or objects (come here / come to me / go near the chair), saving and listing locations, scanning the surroundings, stopping",
        examples=["go to the kitchen", "find the bottle", "come here", "go near the chair", "save this spot as dining area", "stop"],
        prompt=prompts.NAVIGATE_PROMPT,
        tools=NAVIGATE_TOOLS,
    ),
    AgentSpec(
        name="status",
        description="robot battery level, hardware state, what the robot is currently doing",
        examples=["what's your battery?", "how are you doing?", "are you okay?"],
        prompt=prompts.STATUS_PROMPT,
        tools=STATUS_TOOLS,
    ),
    AgentSpec(
        name="swiggy",
        description="restaurant food delivery via Swiggy — restaurant search, browsing menus, managing cart, placing food orders",
        examples=["order pizza", "what restaurants are nearby?", "add to cart"],
        prompt=prompts.SWIGGY_PROMPT,
        tools=SWIGGY_TOOLS,
        mcp_provider="swiggy_food",
        unavailable_note=prompts.SWIGGY_FOOD_UNAVAILABLE_NOTE,
    ),
    AgentSpec(
        name="instamart",
        description="groceries and household essentials delivered via Swiggy Instamart — product search, cart, quick-commerce orders",
        examples=["order milk and eggs", "get me atta and dish soap", "I need groceries delivered"],
        prompt=prompts.INSTAMART_PROMPT,
        tools=INSTAMART_TOOLS,
        mcp_provider="swiggy_instamart",
        unavailable_note=prompts.INSTAMART_UNAVAILABLE_NOTE,
    ),
    AgentSpec(
        name="dineout",
        description="eating OUT — restaurant table reservations, dining deals, booking via Swiggy Dineout (not delivery)",
        examples=["book a table for two tonight", "any dinner deals nearby?", "reserve at that pizza place for Saturday"],
        prompt=prompts.DINEOUT_PROMPT,
        tools=DINEOUT_TOOLS,
        mcp_provider="swiggy_dineout",
        unavailable_note=prompts.DINEOUT_UNAVAILABLE_NOTE,
    ),
    AgentSpec(
        name="tracker",
        description="checking delivery status, order ETA, tracking a Swiggy food or Instamart grocery order",
        examples=["where's my order?", "how long until delivery?", "track my food"],
        prompt=prompts.TRACKER_PROMPT,
        tools=TRACKER_TOOLS,
        # Tracker reads both providers' tracking tools but owns neither, so it
        # refreshes tokens without ever swapping in an unavailable note — it
        # still has set_active_order and navigate_to_pose to work with.
        refresh_mcp=True,
    ),
    AgentSpec(
        name="knowledge",
        description="questions answerable from the household's saved documents — appliance manuals, saved notes/instructions, warranties, papers sent to the robot; also listing what documents exist",
        examples=["how do I descale the coffee machine?", "what does error E4 mean on the washer?", "what documents do you have?"],
        prompt=prompts.KNOWLEDGE_PROMPT,
        tools=KNOWLEDGE_AGENT_TOOLS,
    ),
    AgentSpec(
        name="briefing",
        description="the household briefing — a spoken summary of today's reminders, weather and lists (scheduled morning briefing or asked for directly)",
        examples=["give me my briefing", "what's my day look like?", "[SYSTEM] Morning briefing"],
        prompt=prompts.BRIEFING_PROMPT,
        tools=BRIEFING_TOOLS,
        context=briefing_context,
    ),
)

SPECS: dict[str, AgentSpec] = {spec.name: spec for spec in _SPECS}

# Routing destinations only — what the supervisor picks between and what the
# handover routing notes describe. Historically named AGENTS; kept as a plain
# name→meta mapping for the prompt/notes code that only wants the copy.
AGENTS: dict[str, dict] = {
    name: {"description": spec.description, "examples": spec.examples}
    for name, spec in SPECS.items()
    if spec.routable
}

STICKY_ELIGIBLE: frozenset[str] = frozenset(
    name for name, spec in SPECS.items() if spec.sticky
)

# Consistency with the leaf module the handover grammar is built from. A new
# agent added in only one of the two files fails here, at import, rather than
# as a silently-dropped handover at 2am.
assert set(AGENTS) == set(AGENT_IDS), (
    f"registry/agent_ids drift: {set(AGENTS) ^ set(AGENT_IDS)}"
)
assert set(SPECS) == set(ROUTABLE), (
    f"registry is missing a routable target: {set(SPECS) ^ set(ROUTABLE)}"
)


def build_supervisor_agent_list() -> str:
    """Return the agents block for the supervisor prompt."""
    return "\n".join(
        f'- "{name}" : {meta["description"]}' for name, meta in AGENTS.items()
    )
