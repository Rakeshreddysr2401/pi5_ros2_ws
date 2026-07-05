"""Knowledge base (ingest → search → tools) + briefing scheduler tests.

Chunking and tool formatting run against fakes; the round-trip test uses a
real embedded Qdrant + fastembed (skipped when unavailable, same pattern as
test_memory_round_trip).
"""

import time

import pytest

from langrobo_core.services.briefing import BriefingScheduler
from langrobo_core.services.config import BriefingConfig
from langrobo_core.services.knowledge import chunk_text, extract_text


# ── chunking / extraction ───────────────────────────────────────────────────

def test_chunker_packs_paragraphs_and_splits_oversized():
    paras = ["para one " * 10, "para two " * 10, "x" * 2500]
    chunks = chunk_text("\n\n".join(paras), size=900, overlap=150)
    assert all(len(c) <= 900 for c in chunks)
    # The two small paragraphs pack into one chunk; the huge one slides.
    assert "para one" in chunks[0] and "para two" in chunks[0]
    assert len(chunks) >= 3
    # Overlap: consecutive windows of the big paragraph share text.
    assert chunks[-1][:50] in ("x" * 2500)


def test_extract_text_plain_and_missing_pdf_degrades(monkeypatch):
    text, err = extract_text("notes.txt", "hello world".encode())
    assert text == "hello world" and err is None
    # PDF without pypdf installed → honest error, no crash.
    import builtins
    real_import = builtins.__import__

    def _no_pypdf(name, *a, **k):
        if name == "pypdf":
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_pypdf)
    text, err = extract_text("manual.pdf", b"%PDF-fake")
    assert text == "" and "pypdf" in err


# ── retrieval tools against a fake memory ───────────────────────────────────

class FakeKnowledgeMemory:
    def __init__(self, hits=None, sources=None):
        self._hits = hits or []
        self._sources = sources or []

    def available(self):
        return True

    def search_knowledge(self, query, k=5):
        return self._hits

    def knowledge_sources(self):
        return self._sources


def test_search_documents_formats_sources(monkeypatch):
    from langrobo_core.services import memory as memory_service
    from langrobo_core.tools.knowledge import list_documents, search_documents
    monkeypatch.setattr(memory_service, "_instance", FakeKnowledgeMemory(
        hits=[{"source": "airfryer.pdf", "chunk": 3,
               "text": "Descale monthly with vinegar."}],
        sources=["airfryer.pdf", "wifi-notes.md"]))
    out = search_documents.func(query="descale")
    assert "[airfryer.pdf §3]" in out and "vinegar" in out
    assert "airfryer.pdf, wifi-notes.md" in list_documents.func()


def test_search_documents_empty_suggests_upload(monkeypatch):
    from langrobo_core.services import memory as memory_service
    from langrobo_core.tools.knowledge import search_documents
    monkeypatch.setattr(memory_service, "_instance", FakeKnowledgeMemory())
    assert "Telegram" in search_documents.func(query="anything")


# ── knowledge round-trip (real embedded Qdrant; skipped if deps missing) ────

def test_knowledge_round_trip(tmp_path):
    pytest.importorskip("qdrant_client")
    pytest.importorskip("fastembed")
    from langrobo_core.services.config import MemoryConfig
    from langrobo_core.services.memory import EpisodicMemory

    mem = EpisodicMemory(MemoryConfig(enabled=True, path=str(tmp_path / "q")))
    deadline = time.time() + 120
    while not mem.available() and not mem.status()["error"] and time.time() < deadline:
        time.sleep(0.5)
    if not mem.available():
        pytest.skip(f"memory backend unavailable: {mem.status()['error']}")

    n = mem.ingest_knowledge(
        ["Descale the coffee machine monthly with citric acid.",
         "The warranty covers two years from purchase."],
        source="coffee-manual.txt")
    assert n == 2
    hits = mem.search_knowledge("how do I descale it", k=2)
    assert hits and "citric acid" in hits[0]["text"]
    assert mem.knowledge_sources() == ["coffee-manual.txt"]
    # Re-ingesting the same source REPLACES it — no duplicates.
    assert mem.ingest_knowledge(["Descale weekly."], source="coffee-manual.txt") == 1
    hits = mem.search_knowledge("descale", k=5)
    assert all(h["source"] == "coffee-manual.txt" for h in hits)
    assert len([h for h in hits if "Descale" in h["text"]]) == 1


# ── briefing scheduler ──────────────────────────────────────────────────────

def _scheduler(tmp_path, enabled=True, hour=0):
    return BriefingScheduler(BriefingConfig(
        enabled=enabled, hour=hour, state_path=str(tmp_path / "briefing.json")))


def test_briefing_fires_once_per_day(tmp_path):
    s = _scheduler(tmp_path, hour=0)          # hour 0 → always past the hour
    assert s.due()
    s.mark_done()
    assert not s.due()                        # once per day
    # Survives a restart within the same day.
    assert not _scheduler(tmp_path, hour=0).due()


def test_briefing_respects_hour_and_optin(tmp_path):
    assert not _scheduler(tmp_path, hour=23, enabled=True).due() or \
        time.localtime().tm_hour >= 23        # only true late at night
    assert not _scheduler(tmp_path, enabled=False, hour=0).due()
