"""agents/supervisor.py's build_llm_call — shape parity needed for the
cache warmer to actually warm the supervisor's pinned slot (off-robot,
no LLM server: bind_tools()/SystemMessage construction only)."""

from langchain_core.messages import HumanMessage, SystemMessage

import langrobo_core.graph  # noqa: F401 — import order avoids a pre-existing
# agents<->graph circular import (agents.supervisor imports graph.state;
# graph.build imports agents.supervisor.supervisor_node) that only surfaces
# when an agent module is the very first thing imported.
from langrobo_core.agents.supervisor import _get_prompt, build_llm_call


def test_build_llm_call_returns_tool_bound_llm_and_matching_prompt():
    llm, msgs = build_llm_call([HumanMessage(content="hello")])
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == _get_prompt()
    assert any(isinstance(m, HumanMessage) and m.content == "hello" for m in msgs)
    bound_tools = getattr(llm, "kwargs", {}).get("tools") or getattr(llm, "bound", None)
    assert bound_tools is not None or hasattr(llm, "bound")


def test_build_llm_call_is_append_only_on_warmup_tail():
    history = [HumanMessage(content="hello")]
    _, base_msgs = build_llm_call(history)
    _, warm_msgs = build_llm_call(history + [HumanMessage(content="(warmup)")])
    assert [m.content for m in warm_msgs[:len(base_msgs)]] == \
           [m.content for m in base_msgs]
