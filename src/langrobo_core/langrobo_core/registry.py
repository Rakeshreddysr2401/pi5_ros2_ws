"""Agent registry — one AgentSpec per agent, and nothing about an agent lives
anywhere else.

Adding an agent is exactly two edits: a name in `agent_ids.py`, and one
AgentSpec here. The graph topology, the handover grammar, the sticky-entry
set, the agent's rendered tool block and its llama.cpp KV slot all derive from
this file. `tests/test_smoke.py` and
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
model. With three agents and three slots, nothing ever evicts anything.

    slot 0  chat          the default responder, and the router
    slot 1  local_agent   image prefix — kept away from the text agents
    slot 2  navigate      latency-sensitive: a movement command is waiting

Start the server with `--parallel 4` (slot 3 is the vision-tool slot: tools/survey.py). Fewer slots still works — slots are
assigned modulo the server's real count at startup (services/llm.py), so a
2-slot server just means two agents share, at the old cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from . import prompts
from .agent_ids import AGENT_IDS, ROUTABLE
from .tools import CHAT_TOOLS, LOCAL_AGENT_TOOLS, NAVIGATE_TOOLS


# ── Dynamic prompt tails ─────────────────────────────────────────────────────
# Appended at the END of the system prompt, never the middle: the static prefix
# in front of them is what stays resident in the agent's llama.cpp KV slot.
# Only the DATE goes in — the clock is the get_current_time tool, because a
# per-minute timestamp made consecutive turns diverge mid-prompt and re-prefill
# every ~2k tokens on each minute tick (~20s on the 12B model).

def _today_line() -> str:
    today = datetime.now().strftime("%A %B %d, %Y").replace(" 0", " ")
    return (f"\n== TODAY ==\nToday's date: {today}."
            " The time now is stamped on the user's message as [Time now: ...] -- use it;"
            " call get_current_time only when a message has no stamp.\n")


# ── The spec ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentSpec:
    """Everything the system needs to know about one agent.

    description / examples  routing copy — feeds build_agent_list() and the
                            handover routing notes, i.e. what chat routes on.
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
    intent_examples         more utterances for the Pi's entry classifier
                            (routing/intent.py) on top of `examples`. NEVER
                            rendered into a prompt -- growing this list costs
                            no tokens and leaves every KV cache untouched.
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
    intent_examples: tuple[str, ...] = ()


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="chat",
        description="general questions, web search, the time, robot status, small talk, and anything not about what the camera sees or about moving",
        examples=["what's the weather?", "tell me a joke", "what time is it?",
                  "how are you doing?", "message mom that I'll be late"],
        prompt=prompts.CHAT_PROMPT,
        tools=CHAT_TOOLS,
        slot=0,
        sticky=True,
        context=_today_line,
        intent_examples=(
            "set a timer for 10 minutes", "remind me to call amma at six",
            "add milk to the shopping list", "play a song", "stop the music",
            "turn the volume up", "who is the prime minister of india",
            "where did you see my bag earlier", "where is my bag",
            "what did you see in the kitchen before", "thank you", "good morning",
            "good night", "what is your name", "search the web for cricket scores",
            "what's on my to do list", "what's your battery status",
            "what's the date today", "send me a photo",
            "send a message to dad on telegram", "what can you do",
            "I am leaving", "that's enough", "nothing",
            # memory (tools/memory.py) -- personal facts are chat's
            "remember that the keys are in the drawer", "remember that I like filter coffee",
            "when is my father's birthday", "what does amma like to drink",
            "what is the wifi password", "do you remember what I told you",
            "what medicine does grandma take", "forget what I said about the keys"),
    ),
    AgentSpec(
        name="local_agent",
        description="anything about what the robot SEES — scene description, object/person detection, visual queries, and follow-up questions about the same scene (reasons over the actual camera image and remembers it)",
        examples=["what do you see?", "is there anyone in the room?",
                  "did he wear spectacles?", "send mom a photo of the room"],
        prompt=prompts.LOCAL_AGENT_PROMPT,
        tools=LOCAL_AGENT_TOOLS,
        slot=1,
        sticky=True,
        keep_images=True,
        intent_examples=(
            "what is in front of you", "what are you looking at", "describe the room",
            "how far is the water bottle from you", "how far away is the chair",
            "what colour is the bag", "is the door open",
            "look again, what do you see now", "how many chairs can you see",
            "is the light on", "read what is written on that box",
            "which way is the spray can", "take a fresh look", "can you see my phone",
            "what is on the table", "is anyone sitting on the sofa",
            "send me a pic of what you are seeing", "send me a photo"),
    ),
    AgentSpec(
        name="navigate",
        description="moving the robot — driving somewhere, going to a saved place, approaching a described object, turning, scanning the surroundings, saving a location, stopping",
        examples=["go to the kitchen", "come closer", "go to the red bottle",
                  "turn left 90 degrees", "save this spot as dining area", "stop"],
        prompt=prompts.NAVIGATE_PROMPT,
        tools=NAVIGATE_TOOLS,
        slot=2,
        # Sticky since 2026-09-08. It was not, because navigate ends its turn
        # with a plain confirmation and no routing opinion. But with the regex
        # fast path gone every movement command costs chat + handover +
        # navigate, and a multi-step drive ("forward a metre" ... "now turn
        # left") paid that twice. Sticky makes the follow-up ONE call.
        # The trade: a non-movement follow-up now lands here first and must be
        # handed back to chat — NAVIGATE_PROMPT rule 6 is what makes that
        # reliable, so the two must stay in step.
        sticky=True,
        intent_examples=(
            "save this spot as dining area", "move forward one metre",
            "go back 50 centimetres", "rotate 180 degrees", "come here",
            "go near the chair", "can you go near to the bottle", "drive to the door",
            "turn right a little", "go back to where you started", "move back a bit",
            "go near the bag and tell me what is on it",
            "go to the sofa and send me a photo", "look around the room",
            "come out of the room", "go to the charging dock",
            "take me to the bedroom", "move forward a little", "spin around",
            "back up", "go 10 centimetres ahead", "please go near it",
            "go near the red bag", "go to the backpack", "go near the umbrella",
            "approach the table", "find the bottle and go there",
            "go near the black bag and send me a pic", "go to the person"),
    ),
)

SPECS: dict[str, AgentSpec] = {spec.name: spec for spec in _SPECS}

# Routing copy — what chat's prompt picks between and what the handover
# routing notes describe. Every agent is a destination now; there is no
# router sitting outside the set.
AGENTS: dict[str, dict] = {
    name: {"description": spec.description, "examples": spec.examples}
    for name, spec in SPECS.items()
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
# ...and none may take the vision-TOOL slot, where one-shot photo prompts
# (search views, locate, the photo survey) run so they never overwrite an
# agent's cache -- local_agent's above all, which holds its photos.
from .tools.survey import VISION_TOOL_SLOT  # noqa: E402
assert VISION_TOOL_SLOT not in SLOTS.values(), (
    f"an agent claims the vision-tool slot {VISION_TOOL_SLOT}: {SLOTS}")


def build_agent_list(exclude: str = "") -> str:
    """The routing block a prompt uses to describe the OTHER agents.

    chat renders this so its hand-over rules cannot drift from the registry —
    the descriptions an agent routes on are the ones declared above it.
    """
    return "\n".join(
        f'- "{name}" : {meta["description"]}'
        for name, meta in AGENTS.items() if name != exclude
    )


def warm_order(entry: str, all_agents: bool = True) -> list[str]:
    """The agents the idle cache warmer prefills, in order: the others first,
    `entry` (where the next turn will land) LAST.

    All of them, because a handover lands on the target agent with a prompt
    that has not been read since the history last changed: the first "go to
    the kitchen" or "what do you see" after boot paid a full ~2.5k-token
    read. Entry last, because a server whose slots evict each other (the
    Mac's, until --swa-full; scripts/llm_cache_check.py) then still keeps the
    one the next turn needs."""
    if entry not in SPECS:
        entry = "chat"
    others = [name for name in SPECS if name != entry] if all_agents else []
    return others + [entry]

