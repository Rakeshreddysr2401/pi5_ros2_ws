"""Clean message history before passing to agent LLMs.

Strips handover routing noise (empty AIMessages with handover tool_calls,
handover ToolMessages) so agents see a clean conversation without internal
plumbing.

CACHE INVARIANT — the projection must be APPEND-ONLY: for any shared log L and
suffix S, project(L) must be a prefix of project(L + S). Every agent's llama.cpp
slot caches the KV of its previous prompt; if anything in the middle of the
projection changes or disappears between turns, the prompt diverges at that
point and everything after it re-prefills (~20s for text, far worse when the
divergence sits before a cached camera frame on local_agent's slot). This is
why ALL historical SystemMessages are kept: they are routing notes written by
handle_handover, and dropping an old one would shift the cached prefix.
The per-message transforms below (handover stripping, image stripping) are
deterministic functions of each message alone, so they preserve the invariant.
"""

import logging
import time

from langchain_core.messages import AIMessage, ToolMessage

_FALLBACK_MESSAGE = "I'm having trouble responding right now. Please try again in a moment."
_OFFLINE_MESSAGE = (
    "My brain server is offline right now, and I have no backup model configured. "
    "Please check the LLM server."
)
_ALL_DOWN_MESSAGE = (
    "I can't reach my brain right now — both my local model and the cloud backup "
    "are unreachable. Please check the network."
)


def safe_invoke(llm, messages: list, logger: logging.Logger, retries: int = 1,
                agent: str | None = None) -> AIMessage:
    """invoke() with retry + local-first cloud fallback (services.llm policy).

    1. Primary first (one retry — a transient hiccup shouldn't surface as a
       spoken failure when the very next attempt would succeed). While the
       primary is inside its post-failure cooldown, this step is skipped so
       every turn doesn't pay a connect timeout.
    2. On failure: mark the primary down (connection-class errors only) and
       try the configured cloud fallback, if any.
    3. Everything failed: return a spoken degraded message — never raise.

    `agent`: optional name (e.g. "local_agent") logged alongside which LLM
    (primary/fallback) actually answered — a cloud fallback has no KV-cache
    slot, so a run of fallback answers for one agent is a lead if that
    agent's warm slot later shows an unexplained full re-prefill.
    """
    from ..services import llm as llm_service

    fallback = llm_service.get_fallback_llm()
    primary_error: Exception | None = None

    if llm_service.primary_available() or fallback is None:
        for attempt in range(retries + 1):
            try:
                response = llm.invoke(messages)
                llm_service.report_primary_success()
                # agent/slot only when the caller identified itself —
                # slot_for(None) is the GLOBAL slot and would mis-attribute
                # e.g. local_agent's calls (slot 1) to chat's slot 0.
                extra = {"llm_source": "primary"}
                if agent:
                    extra["agent"] = agent
                    extra["slot"] = llm_service.slot_for(agent)
                logger.info("LLM call answered", extra=extra)
                return response
            except Exception as e:
                primary_error = e
                logger.error("LLM call failed (attempt %d/%d): %s",
                             attempt + 1, retries + 1, e)
                if attempt < retries:
                    time.sleep(0.5)
        if llm_service.is_connection_error(primary_error):
            llm_service.report_primary_failure()
    else:
        logger.info("Primary LLM in cooldown — going straight to fallback")

    if fallback is not None:
        try:
            response = fallback.invoke(messages)
            from ..services import metrics
            metrics.inc("llm_fallback_used_total")
            extra = {"llm_source": "fallback"}
            if agent:
                extra["agent"] = agent
            logger.warning("Answered via cloud fallback LLM", extra=extra)
            return response
        except Exception as e:
            logger.error("Fallback LLM failed too: %s", e)
            return AIMessage(content=_ALL_DOWN_MESSAGE)

    if primary_error is not None and llm_service.is_connection_error(primary_error):
        return AIMessage(content=_OFFLINE_MESSAGE)
    return AIMessage(content=_FALLBACK_MESSAGE)


def _strip_images(msg):
    """Return a copy of msg with any image_url content blocks removed.

    Multimodal messages carry list content like
    [{"type": "text", ...}, {"type": "image_url", ...}].  Non-vision agents get
    a text-only projection of the shared log: drop the image blocks, collapse
    the remaining text.  Messages with plain string content pass through.
    """
    content = msg.content
    if not isinstance(content, list):
        return msg
    texts = [
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    return msg.model_copy(update={"content": " ".join(t for t in texts if t)})


def prepare_messages_for_agent(
    messages: list,
    keep_images: bool = False,
) -> list:
    """Remove handover routing noise from the shared log (append-only, see above).

    Keeps:
    - All HumanMessages
    - All SystemMessages (historical routing notes — dropping one would shift
      the cached prompt prefix; handle_handover words them so stale ones read
      as history, not current instructions)
    - AIMessages with text content (actual agent responses)
    - AIMessages with non-handover tool_calls (agent's own tools)
    - ToolMessages from non-handover tools

    keep_images: when False, strips image_url blocks so non-vision agents get a
    text-only projection of the (possibly multimodal) shared conversation.
    """
    filtered = []

    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == "handover":
            continue

        if isinstance(msg, AIMessage) and msg.tool_calls:
            handover_calls = [tc for tc in msg.tool_calls if tc["name"] == "handover"]
            other_calls = [tc for tc in msg.tool_calls if tc["name"] != "handover"]

            if handover_calls and not other_calls and not msg.content:
                continue

            if handover_calls and not other_calls and msg.content:
                filtered.append(msg.model_copy(update={"tool_calls": []}))
                continue

            if handover_calls and other_calls:
                filtered.append(AIMessage(
                    content=msg.content,
                    tool_calls=other_calls,
                    id=msg.id,
                ))
                continue

        filtered.append(msg)

    if not keep_images:
        filtered = [_strip_images(m) for m in filtered]

    return filtered
