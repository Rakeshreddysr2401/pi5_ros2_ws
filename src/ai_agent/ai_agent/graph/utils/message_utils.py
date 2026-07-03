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


def safe_invoke(llm, messages: list, logger: logging.Logger, retries: int = 1) -> AIMessage:
    """invoke() with one retry — a transient network/server hiccup shouldn't
    surface as a spoken failure when the very next attempt would succeed."""
    for attempt in range(retries + 1):
        try:
            return llm.invoke(messages)
        except Exception as e:
            logger.error("LLM call failed (attempt %d/%d): %s", attempt + 1, retries + 1, e)
            if attempt < retries:
                time.sleep(0.5)
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
