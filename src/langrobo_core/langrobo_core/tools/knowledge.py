"""Knowledge-base retrieval tools — the knowledge agent's eyes into the
household's ingested documents. Pure zone.

Ingestion happens elsewhere (Telegram document upload / scripts/ingest_docs.py
→ services/knowledge.py); these tools only read.
"""

from langchain_core.tools import tool

from ..services import memory as memory_service


@tool
def search_documents(query: str) -> str:
    """Search the household's saved documents (manuals, notes, papers that
    were sent to the robot) for passages relevant to the query.

    Use for questions about appliance manuals, saved instructions, warranties,
    recipes — anything someone uploaded. Rephrase and search again if the
    first pass misses. Answer from the passages and mention the source file."""
    mem = memory_service.get()
    if mem is None or not mem.available():
        return "The document knowledge base is not available right now."
    hits = mem.search_knowledge(query, k=5)
    if not hits:
        return ("Nothing relevant in the saved documents. If the user expected "
                "a match, tell them which documents exist (list_documents) and "
                "that new ones can be sent to the robot on Telegram.")
    lines = []
    for h in hits:
        lines.append(f"[{h.get('source', 'document')} §{h.get('chunk', '?')}] "
                     f"{h.get('text', '')}")
    return "\n\n".join(lines)


@tool
def list_documents() -> str:
    """List the documents currently in the household knowledge base."""
    mem = memory_service.get()
    if mem is None or not mem.available():
        return "The document knowledge base is not available right now."
    sources = mem.knowledge_sources()
    if not sources:
        return ("No documents saved yet. Anyone in the household can send a "
                ".pdf, .txt or .md file to the robot on Telegram to add one.")
    return "Saved documents: " + ", ".join(sources)


KNOWLEDGE_TOOLS = [search_documents, list_documents]
