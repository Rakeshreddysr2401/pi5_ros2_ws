"""One agent node implementation, built from an AgentSpec.

Every responder is the same twenty lines with three words changed — bind the
tools, project the history, invoke, tag the state. They are this factory plus a
row in registry.py; `supervisor.py` stays hand-written because it is not a
responder at all (forced tool_choice, single-handover normalisation).

Two things happen once at import and never again, both to protect the
llama.cpp KV cache: `{tools}` is substituted into the prompt, and the dynamic
context tail is the ONLY part recomputed per call — appended at the end, so
the cached static prefix in front of it survives.
"""

import logging

from langchain_core.messages import SystemMessage

from ..prompts import render_tools
from ..registry import AgentSpec
from ..services.llm import get_llm
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)


def build_agent(spec: AgentSpec):
    """Return (node_fn, build_llm_call) for one agent.

    build_llm_call is exported because agent_node's cache warmer must send a
    prompt that is byte-identical to the next real request — same bound tools,
    same system text. Sharing this function is what makes that guaranteed
    rather than aspirational.
    """
    # Substituted, not .format()ed: prompts contain literal braces (JSON
    # examples, tool argument syntax) that str.format would choke on.
    base_prompt = spec.prompt.replace("{tools}", render_tools(spec.tools))

    def build_llm_call(messages: list):
        llm = get_llm(spec.name).bind_tools(spec.tools)
        prompt = base_prompt
        if spec.context is not None:
            prompt += spec.context()
        clean = prepare_messages_for_agent(messages, keep_images=spec.keep_images)
        return llm, [SystemMessage(content=prompt)] + clean

    def node(state) -> dict:
        llm, msgs = build_llm_call(state["messages"])
        # Naming the agent is what puts the right llama.cpp slot in the log
        # line: slot_for(None) reports the GLOBAL slot and would misattribute
        # e.g. local_agent's calls (slot 1) to chat's slot 0.
        response = safe_invoke(llm, msgs, logger, agent=spec.name)
        return {"messages": [response], "active_agent": spec.name}

    node.__name__ = f"{spec.name}_node"
    build_llm_call.__name__ = f"{spec.name}_build_llm_call"
    return node, build_llm_call
