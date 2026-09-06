"""The cache warmer only warms anything if it sends a byte-identical prompt.

agent_node prefills a slot by calling the SAME `build_llm_call` the real turn
will use, with `max_tokens=1`. If the warm-up prompt differs from the real one
by a single token the prefix does not match, the server prefills from scratch,
and the warm was pure waste — silently, because everything still works.

These run off-robot with no LLM server: bind_tools() and SystemMessage
construction only.
"""

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from langrobo_core.bridges import StubBridge
from langrobo_core.tools import _bridge

_bridge._instance = None
_bridge.init(StubBridge())

from langrobo_core.agents import BUILD_LLM_CALLS   # noqa: E402
from langrobo_core.agent_ids import AGENT_IDS      # noqa: E402


def test_every_agent_exports_a_build_llm_call():
    """agent_node warms by name; a missing entry is a silent no-warm."""
    assert set(BUILD_LLM_CALLS) == set(AGENT_IDS)


@pytest.mark.parametrize("agent", AGENT_IDS)
def test_prompt_is_a_system_message_and_history_follows(agent):
    llm, msgs = BUILD_LLM_CALLS[agent]([HumanMessage(content="hello")])
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content.strip()
    assert any(isinstance(m, HumanMessage) and m.content == "hello" for m in msgs)


@pytest.mark.parametrize("agent", AGENT_IDS)
def test_the_agents_real_tools_ride_along(agent):
    """The warm must bind the same tool schemas the real call binds — they are
    part of the prefix, so warming without them warms the wrong prompt."""
    from langrobo_core.registry import SPECS
    llm, _ = BUILD_LLM_CALLS[agent]([HumanMessage(content="hi")])
    bound = str(getattr(llm, "kwargs", {}).get("tools", []))
    for tool in SPECS[agent].tools:
        assert tool.name in bound, f"{agent}: {tool.name} not bound"


@pytest.mark.parametrize("agent", AGENT_IDS)
def test_appending_the_warmup_tail_is_append_only(agent):
    """The warm-up adds "(warmup)" at the END. Everything before it — the
    expensive part — must be identical, or the cached prefix diverges."""
    history = [HumanMessage(content="hello")]
    _, base_msgs = BUILD_LLM_CALLS[agent](history)
    _, warm_msgs = BUILD_LLM_CALLS[agent](history + [HumanMessage(content="(warmup)")])
    assert [str(m.content) for m in warm_msgs[:len(base_msgs)]] == \
           [str(m.content) for m in base_msgs]


def test_the_dynamic_tail_is_the_only_thing_that_moves():
    """chat appends today's date via AgentSpec.context. That tail is allowed to
    change between calls; the static prefix in front of it is not — that split
    is the whole KV-cache discipline in prompts.py."""
    from langrobo_core.registry import SPECS
    assert SPECS["chat"].context is not None
    _, a = BUILD_LLM_CALLS["chat"]([HumanMessage(content="hi")])
    _, b = BUILD_LLM_CALLS["chat"]([HumanMessage(content="hi")])
    prompt_a, prompt_b = a[0].content, b[0].content
    assert prompt_a == prompt_b                      # same second, same tail
    assert "== TODAY ==" in prompt_a
    # The date block is last: everything before it is the cacheable prefix.
    assert prompt_a.index("== TODAY ==") > len(prompt_a) * 0.5
