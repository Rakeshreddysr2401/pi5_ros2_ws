"""One agent node implementation, built from an AgentSpec.

Every agent is the same twenty lines with three words changed — bind the
tools, project the history, invoke, tag the state. They are this factory plus a
row in registry.py, with no exceptions: the one hand-written node was the
router, and it is gone.

Two things happen once at import and never again, both to protect the
llama.cpp KV cache: `{tools}` is substituted into the prompt, and the dynamic
context tail is the ONLY part recomputed per call — appended at the end, so
the cached static prefix in front of it survives.
"""

import logging
import re

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from ..prompts import render_tools
from ..registry import AgentSpec
from ..services.llm import get_llm
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke
from ..utils.speech_stream import strip_thought_residue

logger = logging.getLogger(__name__)


# Sentences in a tool result that talk TO THE MODEL, not to the user.
_FOR_THE_MODEL = re.compile(
    r"tell (the user|them)|do not|don't|never|the robot has moved|camera view has "
    r"changed|any photo|call look|\(tool|nothing i do", re.IGNORECASE)


def reply_from_tool(text: str, max_sentences: int = 2) -> str:
    """The user-facing part of a tool result: its first sentences, minus the
    ones addressed to the model. "" when nothing is left."""
    sentences = re.split(r"(?<=[.!?])\s+", " ".join((text or "").split()))
    keep = [x for x in sentences if x and not _FOR_THE_MODEL.search(x)]
    return " ".join(keep[:max_sentences])


def _is_blank(msg) -> bool:
    """No tool call and no text worth keeping (markers and "thought" only)."""
    if getattr(msg, "tool_calls", None):
        return False
    content = msg.content
    if isinstance(content, list):
        return False          # multimodal content is never this failure
    return not strip_thought_residue(content or "").strip()


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
        if _is_blank(response):
            # Gemma 4 on this llama.cpp sometimes wraps its answer in channel
            # markers; STREAMED, the server then files the whole answer as
            # hidden reasoning and the reply arrives empty (a Telegram user got
            # "Sorry, I couldn't come up with a reply" 2026-09-27). Unstreamed,
            # the same answer comes back in the text with its markers, which
            # strip_thought_residue removes. Only this failure pays the retry.
            logger.warning("empty reply from %s — retrying once unstreamed", spec.name)
            retry = get_llm(spec.name)
            if getattr(retry, "streaming", False):
                retry = retry.model_copy(update={"streaming": False})
            response = safe_invoke(retry.bind_tools(spec.tools), msgs, logger, agent=spec.name)
            if _is_blank(response) and msgs and isinstance(msgs[-1], ToolMessage):
                # Still nothing, straight after a tool: the tool's own words
                # are the answer ("I can see the white box -- about 1.1 m
                # away. On my way..."). Seen 3 times on 2026-09-27, each
                # leaving a Telegram user with no reply at all.
                said = reply_from_tool(str(msgs[-1].content))
                if said:
                    logger.warning("empty reply from %s after retry — answering "
                                   "with the tool's result", spec.name)
                    response = AIMessage(content=said)
        if isinstance(response.content, str):
            cleaned = strip_thought_residue(response.content)
            if cleaned != response.content:   # never spoken, sent or kept in history
                response = response.model_copy(update={"content": cleaned})
        return {"messages": [response], "active_agent": spec.name}

    node.__name__ = f"{spec.name}_node"
    build_llm_call.__name__ = f"{spec.name}_build_llm_call"
    return node, build_llm_call
