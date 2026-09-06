"""Agent registry — one AgentSpec per agent, and nothing about an agent lives
anywhere else.

Adding an agent is exactly two edits: a name in `agent_ids.py`, and one
AgentSpec here. The graph topology, the handover grammar, the supervisor's
routing table, the sticky-entry set, the agent's rendered tool block and its
llama.cpp KV slot all derive from this file. `tests/test_smoke.py` and
`tests/test_prompt_contract.py` fail if they ever drift apart.

The prompt in a spec is a TEMPLATE: its `{tools}` placeholder is filled from
`spec.tools` at import time (see agents/factory.py), so a prompt cannot name a
tool the agent is not bound to. Substitution happens once per process, which
keeps the KV-cache prefix rule in prompts.py intact.

── KV SLOTS ────────────────────────────────────────────────────────────────
`slot` is the agent's llama.cpp `id_slot`. It is declared HERE, next to the
agent, because it is a property of the agent and nothing else — it used to be
five hand-maintained ROS parameters plus a fold-when-out-of-range algorithm,
which nobody could hold in their head.

Why it matters: every agent's system prompt is a different ~1-2k token prefix.
A llama.cpp server started with `--parallel N` keeps N independent KV caches.
Pin each agent to its own and its prefix stays resident, so a turn only
prefills the NEW tokens. Share a slot between two agents and each call evicts
the other's prefix — measured at ~18-50s of re-prefill per turn on the 12B
model. With four agents and four slots, nothing ever evicts anything.

    slot 0  supervisor    runs on EVERY turn, so it must never be evicted
    slot 1  chat          the default responder
    slot 2  local_agent   image prefix — kept away from the text agents
    slot 3  navigate      latency-sensitive: a movement command is waiting

Start the server with `--parallel 4`. Fewer slots still works — slots are
assigned modulo the server's real count at startup (services/llm.py), so a
2-slot server just means two agents share, at the old cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from . import prompts
from .agent_ids import AGENT_IDS, ROUTABLE
from .tools import (
    CHAT_TOOLS,
    LOCAL_AGENT_TOOLS,
    NAVIGATE_TOOLS,
    SUPERVISOR_TOOLS,
)


# ── Dynamic prompt tails ─────────────────────────────────────────────────────
# Appended at the END of the system prompt, never the middle: the static prefix
# in front of them is what stays resident in the agent's llama.cpp KV slot.
# Only the DATE goes in — the clock is the get_current_time tool, because a
# per-minute timestamp made consecutive turns diverge mid-prompt and re-prefill
# every ~2k tokens on each minute tick (~20s on the 12B model).

def _today_line() -> str:
    today = datetime.now().strftime("%A %B %d, %Y").replace(" 0", " ")
    return (f"\n== TODAY ==\nToday's date: {today}."
            " For the clock time, call get_current_time.\n")


# ── The spec ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentSpec:
    """Everything the system needs to know about one agent.

    description / examples  routing copy — feeds the supervisor's agent list and
                            the handover routing notes.
    prompt                  system-prompt template containing `{tools}`.
    tools                   the bound tool set; also what fills `{tools}`.
    slot                    llama.cpp KV-cache slot (id_slot). See the module
                            docstring — this is the whole parallel-cache design.
    sticky                  may a follow-up turn re-enter this agent directly,
                            skipping the routing hop? Only for agents that
                            answer in plain text AND can re-route a topic change
                            themselves (see graph/turn_entry.py). This is worth
                            one whole LLM round-trip per follow-up turn.
    keep_images             feed this agent the image-preserving projection of
                            the shared log (multimodal agents only).
    context                 optional callable returning the dynamic prompt tail.
    routable                False only for the supervisor, which routes rather
                            than being routed to.
    """

    name: str
    description: str
    examples: list[str]
    prompt: str
    tools: list
    slot: int
    sticky: bool = False
    keep_images: bool = False
    context: Optional[Callable[[], str]] = None
    routable: bool = True


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="supervisor",
        description="",           # the supervisor is not a routing destination
        examples=[],
        prompt=prompts.SUPERVISOR_PROMPT_TEMPLATE,
        tools=SUPERVISOR_TOOLS,
        slot=0,
        routable=False,
    ),
    AgentSpec(
        name="chat",
        description="general questions, web search, the time, robot status, small talk, and anything not about what the camera sees or about moving",
        examples=["what's the weather?", "tell me a joke", "what time is it?",
                  "how are you doing?", "message mom that I'll be late"],
        prompt=prompts.CHAT_PROMPT,
        tools=CHAT_TOOLS,
        slot=1,
        sticky=True,
        context=_today_line,
    ),
    AgentSpec(
        name="local_agent",
        description="anything about what the robot SEES — scene description, object/person detection, visual queries, and follow-up questions about the same scene (reasons over the actual camera image and remembers it)",
        examples=["what do you see?", "is there anyone in the room?",
                  "did he wear spectacles?", "send mom a photo of the room"],
        prompt=prompts.LOCAL_AGENT_PROMPT,
        tools=LOCAL_AGENT_TOOLS,
        slot=2,
        sticky=True,
        keep_images=True,
    ),
    AgentSpec(
        name="navigate",
        description="moving the robot — driving somewhere, going to a saved place, approaching a described object, turning, scanning the surroundings, saving a location, stopping",
        examples=["go to the kitchen", "come closer", "go to the red bottle",
                  "turn left 90 degrees", "save this spot as dining area", "stop"],
        prompt=prompts.NAVIGATE_PROMPT,
        tools=NAVIGATE_TOOLS,
        slot=3,
    ),
)

SPECS: dict[str, AgentSpec] = {spec.name: spec for spec in _SPECS}

# Routing destinations only — what the supervisor picks between and what the
# handover routing notes describe.
AGENTS: dict[str, dict] = {
    name: {"description": spec.description, "examples": spec.examples}
    for name, spec in SPECS.items()
    if spec.routable
}

STICKY_ELIGIBLE: frozenset[str] = frozenset(
    name for name, spec in SPECS.items() if spec.sticky
)

# {agent: slot} — handed to services/llm.configure() as the per-agent override
# map. One place produces it, so a new agent cannot forget its slot.
SLOTS: dict[str, int] = {name: spec.slot for name, spec in SPECS.items()}

# Consistency with the leaf module the handover grammar is built from. A new
# agent added in only one of the two files fails here, at import, rather than
# as a silently-dropped handover at 2am.
assert set(AGENTS) == set(AGENT_IDS), (
    f"registry/agent_ids drift: {set(AGENTS) ^ set(AGENT_IDS)}"
)
assert set(SPECS) == set(ROUTABLE), (
    f"registry is missing a routable target: {set(SPECS) ^ set(ROUTABLE)}"
)
# Two agents on one slot evict each other's prompt prefix on every turn. That
# is a real, measurable latency bug and it is invisible at runtime, so it is
# an import-time error instead.
assert len(set(SLOTS.values())) == len(SLOTS), f"duplicate KV slots: {SLOTS}"


def build_supervisor_agent_list() -> str:
    """Return the agents block for the supervisor prompt."""
    return "\n".join(
        f'- "{name}" : {meta["description"]}' for name, meta in AGENTS.items()
    )
