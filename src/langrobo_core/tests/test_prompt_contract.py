"""The prompt layer's standing invariants.

These exist because both failures below actually shipped:

* `TRACKER_PROMPT` documented a `navigate_to` tool for months. No such tool is
  bound to the tracker — every "your order arrived, go to the door" flow emitted
  an invalid call and burned the loop guard. Prompts now render their tool block
  from the bound tool set, and `test_no_ghost_tools` keeps any hand-written
  mention honest.
* Four agents (swiggy, instamart, dineout, tracker) carried NO reply-length rule
  at all, so the model would happily read a restaurant menu into the
  text-to-speech voice. The rule now lives once, in PERSONA/SPEECH_STYLE, and
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

SPEAKING = [name for name in SPECS if name != "supervisor"]


@pytest.mark.parametrize("name", SPEAKING)
def test_every_bound_tool_is_in_the_prompt(name):
    """The agent is told about every tool it can actually call."""
    rendered = RENDERED[name]
    for tool in SPECS[name].tools:
        assert tool.name in rendered, f"{name}: {tool.name} bound but not in prompt"


@pytest.mark.parametrize("name", SPECS)
def test_no_ghost_tools(name):
    """No prompt may name a callable that is not bound to that agent.

    Catches the tracker's `navigate_to` class of bug: any `something(` in the
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


def test_supervisor_has_no_persona_or_speech_style():
    """The supervisor never emits user-facing text; prefill there is pure
    routing latency (see prompts.py header)."""
    assert prompts.SPEECH_STYLE not in RENDERED["supervisor"]
    assert prompts.PERSONA not in RENDERED["supervisor"]


@pytest.mark.parametrize("name", SPEAKING)
def test_prompts_carry_no_markup_the_voice_would_read_aloud(name):
    """speech_stream splits on bare newlines, so a markdown list is READ OUT,
    bullets and all. Prompts must not model the thing they forbid."""
    for line in RENDERED[name].splitlines():
        stripped = line.strip()
        assert not stripped.startswith(("* ", "#", "|")), \
            f"{name}: prompt line looks like markdown the model may imitate: {line!r}"


def test_agent_list_in_supervisor_prompt_matches_the_registry():
    from langrobo_core.registry import build_supervisor_agent_list
    listing = build_supervisor_agent_list()
    for name in AGENT_IDS:
        assert f'"{name}"' in listing, f"{name} unreachable — not in the routing table"


def test_render_tools_reports_an_empty_provider_honestly():
    assert "none available" in prompts.render_tools([])
