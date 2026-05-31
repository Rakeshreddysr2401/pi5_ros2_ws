"""Clean message history before passing to agent LLMs.

Strips handover routing noise (empty AIMessages with handover tool_calls,
handover ToolMessages) and stale SystemMessages so agents see a clean
conversation without internal plumbing.
"""

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

_FALLBACK_MESSAGE = "I'm having trouble responding right now. Please try again in a moment."


def safe_invoke(llm, messages: list, logger: logging.Logger) -> AIMessage:
    try:
        return llm.invoke(messages)
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return AIMessage(content=_FALLBACK_MESSAGE)


def prepare_messages_for_agent(messages: list, keep_all_system_msgs: bool = False) -> list:
    """Remove handover routing noise and stale SystemMessages.

    Keeps:
    - All HumanMessages
    - AIMessages with text content (actual agent responses)
    - AIMessages with non-handover tool_calls (agent's own tools)
    - ToolMessages from non-handover tools
    - SystemMessages: if keep_all_system_msgs=True keeps all from current turn;
      otherwise only the most recent one.
    """
    if keep_all_system_msgs:
        last_human_idx = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
            default=-1,
        )
        should_keep_system = lambda i: i > last_human_idx
    else:
        last_system_idx = max(
            (i for i, m in enumerate(messages) if isinstance(m, SystemMessage)),
            default=-1,
        )
        should_keep_system = lambda i: i == last_system_idx

    filtered = []

    for i, msg in enumerate(messages):
        if isinstance(msg, SystemMessage):
            if should_keep_system(i):
                filtered.append(msg)
            continue

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

    return filtered
