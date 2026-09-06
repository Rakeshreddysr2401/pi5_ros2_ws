"""The prompt layer's standing invariants.

These exist because both failures below actually shipped:

* An agent prompt documented a `navigate_to` tool for months. No such tool was
  bound to that agent — every one of those flows emitted an invalid call and
  burned the loop guard. Prompts now render their tool block from the bound
  tool set, and `test_no_ghost_tools` keeps any hand-written mention honest.
* Several agents carried NO reply-length rule at all, so the model would
  happily read a long list into the text-to-speech voice. The rule now lives
  once, in PERSONA/SPEECH_STYLE, and
  `test_every_speaking_agent_gets_the_speech_contract` keeps it there.
"""

import re

import pytest

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge

_bridge._instance = None
_bridge.init(StubBridge())

from langrobo_core import prompts                    # noqa: E402
from langrobo_core.agent_ids import AGENT_IDS, ROUTABLE   # noqa: E402
from langrobo_core.registry import SPECS             # noqa: E402

# A rendered prompt is what the model actually receives.
RENDERED = {
    name: spec.prompt.replace("{tools}", prompts.render_tools(spec.tools))
    for name, spec in SPECS.items()
}

# Every agent speaks now — the one that never did was the router, and it is
# gone. Kept as a name so the parametrisation still reads as intent.
SPEAKING = list(SPECS)


@pytest.mark.parametrize("name", SPEAKING)
def test_every_bound_tool_is_in_the_prompt(name):
    """The agent is told about every tool it can actually call."""
    rendered = RENDERED[name]
    for tool in SPECS[name].tools:
        assert tool.name in rendered, f"{name}: {tool.name} bound but not in prompt"


@pytest.mark.parametrize("name", SPECS)
def test_no_ghost_tools(name):
    """No prompt may name a callable that is not bound to that agent.

    Catches the `navigate_to` class of bug: any `something(` in the
    prompt that looks like a tool call must be a real tool, an agent name, or
    one of the few prose exceptions below.
    """
    bound = {t.name for t in SPECS[name].tools}
    allowed = bound | set(ROUTABLE) | {
        "e.g", "i.e", "reason", "yourself", "call", "tavily_search",
    }
    mentioned = set(re.findall(r"\b([a-z_][a-z0-9_]{3,})\(", RENDERED[name]))
    ghosts = mentioned - allowed
    assert not ghosts, f"{name}: prompt names non-existent tool(s) {sorted(ghosts)}"


@pytest.mark.parametrize("name", SPEAKING)
def test_every_speaking_agent_gets_the_speech_contract(name):
    """One definition of how the robot talks, shared by every responder."""
    assert prompts.SPEECH_STYLE in RENDERED[name], f"{name} is missing SPEECH_STYLE"


def test_every_agent_shares_one_identity():
    """PERSONA carries the "you are Rakhi, built by Rakesh" block. An agent
    without it falls back to its training and tells users it was made by
    Google (Issues/asked_weather.txt) — so every agent that can answer a user
    must have it, and all of them can now."""
    for name in SPECS:
        assert prompts._IDENTITY in RENDERED[name], f"{name} has no identity block"


@pytest.mark.parametrize("name", SPEAKING)
def test_prompts_carry_no_markup_the_voice_would_read_aloud(name):
    """speech_stream splits on bare newlines, so a markdown list is READ OUT,
    bullets and all. Prompts must not model the thing they forbid."""
    for line in RENDERED[name].splitlines():
        stripped = line.strip()
        assert not stripped.startswith(("* ", "#", "|")), \
            f"{name}: prompt line looks like markdown the model may imitate: {line!r}"


def test_every_agent_is_reachable_from_the_routing_table():
    """chat is the router now. build_agent_list is what a prompt renders to
    describe the others; an agent missing from it is an agent nothing can
    route to, which is the same as an agent that does not exist."""
    from langrobo_core.registry import build_agent_list
    listing = build_agent_list(exclude="chat")
    for name in AGENT_IDS:
        if name == "chat":
            continue
        assert f'"{name}"' in listing, f"{name} unreachable — not in the routing table"


def test_chats_prompt_names_every_agent_it_must_route_to():
    """chat's hand-over rules are hand-written prose. If an agent is added and
    chat is never told about it, the agent exists in the graph and nothing
    ever reaches it."""
    for name in AGENT_IDS:
        if name == "chat":
            continue
        assert name in RENDERED["chat"], (
            f"chat's prompt never mentions {name!r} — nothing can route to it")


def test_render_tools_reports_an_empty_provider_honestly():
    assert "none available" in prompts.render_tools([])
