"""recall_memory() — semantic search over past conversations. Pure zone.

Tool-driven recall keeps the llama.cpp KV-cache discipline intact: retrieved
episodes enter the conversation as a ToolMessage (appended at the tail), never
injected into the system prompt where they would churn the cached prefix every
turn. Household lists/facts don't need this tool — they are always in the
prompt (tools.household).
"""

import time

from langchain_core.tools import tool

from ..services import memory as memory_service


@tool
def recall_memory(query: str) -> str:
    """Search the robot's long-term memory of past conversations.

    Use when the user refers to something from an earlier session that is not
    in the current conversation or in HOUSEHOLD MEMORY: "what did we talk
    about yesterday?", "what did I ask you to do last week?", "did I mention
    my trip?". Returns the most relevant past exchanges with their dates —
    answer from them in your own words."""
    mem = memory_service.get()
    if mem is None or not mem.available():
        return "Long-term memory is not available right now."
    hits = mem.recall(query, k=4)
    if not hits:
        return "I don't have any past conversations matching that."
    lines = []
    for h in hits:
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(h.get("ts", 0)))
        if h.get("fact"):
            # Consolidated household fact (nightly distillation) — no dialogue.
            lines.append(f"[learned {date}] {h['fact']}")
            continue
        who = f" (with {h['person']})" if h.get("person") else ""
        lines.append(f"[{date}]{who} User: {h.get('user', '')} — You replied: {h.get('robot', '')}")
    return "\n".join(lines)
