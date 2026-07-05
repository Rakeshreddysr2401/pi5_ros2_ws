"""Household knowledge base — document ingestion. Pure zone.

Ported from the SubAgents predecessor repo (src/rag/ingestion.py) and adapted
to this codebase's constraints: the EXISTING embedded Qdrant + on-device
fastembed (no Redis, no async stack, nothing leaves the house), and honest
degrades (no pypdf → PDFs unsupported, everything else still works).

Ingest paths:
  - Telegram: send the bot a .pdf/.txt/.md document (owner/family) — the
    poller hands the bytes here on a daemon thread and confirms in chat.
  - CLI: scripts/ingest_docs.py — only while the brain is STOPPED (embedded
    Qdrant is single-process; the script says so instead of corrupting).

Retrieval is the knowledge agent's search_documents tool (tools/knowledge.py).
"""

from __future__ import annotations

import logging

from . import memory as memory_service

logger = logging.getLogger(__name__)

_CHUNK_CHARS = 900      # ~200 tokens — small enough that 4-5 chunks fit a turn
_CHUNK_OVERLAP = 150
_MAX_CHUNKS = 400       # one document may not swamp the collection


def extract_text(filename: str, data: bytes) -> tuple[str, str | None]:
    """(text, error). PDF needs pypdf; txt/md/anything-else decodes as UTF-8."""
    if filename.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            return "", ("PDF support is not installed on this robot "
                        "(pip install pypdf) — send .txt or .md instead.")
        try:
            import io
            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join((page.extract_text() or "")
                               for page in reader.pages), None
        except Exception as e:
            return "", f"Couldn't read that PDF ({type(e).__name__})."
    return data.decode("utf-8", errors="ignore"), None


def chunk_text(text: str, size: int = _CHUNK_CHARS,
               overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Paragraph-preferring splitter — no external dependency. Paragraphs are
    packed up to `size`; oversized paragraphs fall back to a sliding window."""
    chunks: list[str] = []
    buf = ""
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if len(para) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            step = size - overlap
            for start in range(0, len(para), step):
                chunks.append(para[start:start + size])
                if start + size >= len(para):
                    break
        elif len(buf) + len(para) + 2 <= size:
            buf = f"{buf}\n\n{para}" if buf else para
        else:
            chunks.append(buf)
            buf = para
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()][:_MAX_CHUNKS]


def ingest_file(filename: str, data: bytes) -> tuple[int, str | None]:
    """Extract → chunk → embed → store. Returns (chunks_stored, error)."""
    mem = memory_service.get()
    if mem is None or not mem.available():
        return 0, "The knowledge store isn't available right now."
    text, err = extract_text(filename, data)
    if err:
        return 0, err
    chunks = chunk_text(text)
    if not chunks:
        return 0, f"'{filename}' contained no readable text."
    stored = mem.ingest_knowledge(chunks, source=filename)
    logger.info("Knowledge ingest: '%s' → %d chunk(s)", filename, stored)
    return stored, None
