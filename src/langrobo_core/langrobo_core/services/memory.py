"""Episodic memory — Qdrant-backed long-term conversation recall. Pure zone.

Complements (does NOT replace) the in-prompt household memory in
tools.household: small guaranteed-recall facts stay in the prompt; this store
holds what can't fit there — every conversation turn, embedded and searchable
("what did we talk about yesterday?", "what did I ask you last week?").

Design constraints this file honors:
  - KV-cache discipline: recall is TOOL-driven (tools.memory.recall_memory),
    never auto-injected into system prompts — auto-injection would change the
    prompt every turn and defeat the llama.cpp prefix cache.
  - Mac-Mini independence: embeddings run on-device via fastembed (ONNX,
    ~130MB model, CPU) so memory keeps working when the LLM server is down.
  - Never block a turn: writes go through a queue + daemon writer thread;
    embedding latency (~100ms on Pi5) happens off the turn path.
  - Never crash the brain: qdrant-client/fastembed missing or broken →
    memory reports itself unavailable and everything else runs.

Backend: embedded local Qdrant (file-backed, zero server) by default;
set QDRANT_URL (+ QDRANT_API_KEY) to switch to a server / Qdrant Cloud —
config change only. Payload schema includes `person` (nullable) from day one
so P2 face-recognition per-person memory needs no migration, and a reserved
`visual` collection name for P4 visual household memory.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid

from . import metrics
from .config import MemoryConfig

logger = logging.getLogger(__name__)

_EMBED_DIM = 384          # BAAI/bge-small-en-v1.5
_QUEUE_MAX = 256          # writes waiting to be embedded; beyond this we drop oldest
_RECALL_LIMIT_MAX = 10


class EpisodicMemory:
    """Store + recall conversation episodes. Safe to construct always —
    availability is decided lazily on the writer thread (model download and
    store open can be slow; the brain must not wait on them)."""

    def __init__(self, cfg: MemoryConfig):
        self._cfg = cfg
        self._client = None
        self._embedder = None
        self._ready = threading.Event()
        self._failed: str | None = None if cfg.enabled else "disabled by LANGROBO_MEMORY"
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._lock = threading.Lock()
        if cfg.enabled:
            threading.Thread(target=self._writer_loop, daemon=True,
                             name="memory_writer").start()

    # ── Public API (worker thread / tools) ─────────────────────────────────

    def available(self) -> bool:
        return self._ready.is_set()

    def status(self) -> dict:
        return {
            "available": self.available(),
            "error": self._failed,
            "backend": self._cfg.url or self._cfg.path,
            "pending_writes": self._queue.qsize(),
        }

    def record_turn(self, user_text: str, robot_text: str,
                    agent: str = "", person: str | None = None) -> None:
        """Queue one conversation turn for embedding + storage. Non-blocking."""
        if self._failed or not user_text.strip():
            return
        item = {
            "user": user_text.strip()[:2000],
            "robot": (robot_text or "").strip()[:2000],
            "agent": agent,
            "person": person,   # nullable now; P2 face recognition fills it in
            "ts": time.time(),
        }
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:                       # drop the oldest, keep the newest
                self._queue.get_nowait()
                self._queue.put_nowait(item)
            except queue.Empty:
                pass
            metrics.inc("memory_writes_dropped_total")

    def recall(self, query: str, k: int = 4, person: str | None = None) -> list[dict]:
        """Semantic search over stored episodes. Returns [] when unavailable."""
        if not self.available():
            return []
        k = max(1, min(k, _RECALL_LIMIT_MAX))
        try:
            with self._lock:
                vector = self._embed(query)
                hits = self._client.query_points(
                    collection_name=self._cfg.collection,
                    query=vector,
                    limit=k,
                    query_filter=self._person_filter(person),
                ).points
            metrics.inc("memory_recalls_total")
            return [
                {**(h.payload or {}), "score": round(h.score, 3)}
                for h in hits
            ]
        except Exception:
            logger.exception("memory recall failed")
            return []

    def count(self) -> int:
        if not self.available():
            return 0
        try:
            with self._lock:
                return self._client.count(self._cfg.collection).count
        except Exception:
            return 0

    # ── Writer thread ──────────────────────────────────────────────────────

    def _writer_loop(self) -> None:
        try:
            self._open()
        except Exception as e:
            self._failed = f"{type(e).__name__}: {e}"
            logger.warning("Episodic memory unavailable — %s", self._failed)
            return
        self._ready.set()
        metrics.set_gauge("memory_up", 1)
        logger.info("Episodic memory ready (%s, %d episodes)",
                    self._cfg.url or self._cfg.path, self.count())
        while True:
            item = self._queue.get()
            try:
                self._write(item)
                metrics.inc("memory_writes_total")
            except Exception:
                logger.exception("memory write failed")
                metrics.inc("memory_write_errors_total")

    def _open(self) -> None:
        import os
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        from fastembed import TextEmbedding
        self._embedder = TextEmbedding(model_name=self._cfg.embed_model)

        if self._cfg.url:
            self._client = QdrantClient(url=self._cfg.url,
                                        api_key=self._cfg.api_key or None)
        else:
            path = os.path.expanduser(self._cfg.path)
            os.makedirs(path, exist_ok=True)
            self._client = QdrantClient(path=path)

        for name in (self._cfg.collection, self._cfg.visual_collection):
            if not self._client.collection_exists(name):
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=_EMBED_DIM, distance=Distance.COSINE),
                )

    def _embed(self, text: str) -> list[float]:
        return list(next(iter(self._embedder.embed([text]))))

    def _write(self, item: dict) -> None:
        from qdrant_client.models import PointStruct
        text = f"User: {item['user']}\nRobot: {item['robot']}"
        with self._lock:
            vector = self._embed(text)
            self._client.upsert(
                collection_name=self._cfg.collection,
                points=[PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload=item,
                )],
            )

    @staticmethod
    def _person_filter(person: str | None):
        if not person:
            return None
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        return Filter(must=[FieldCondition(key="person", match=MatchValue(value=person))])


# Module-level singleton — same pattern as tools._bridge: the entry point
# calls init() once; tools and agent_node share the instance.
_instance: EpisodicMemory | None = None


def init(cfg: MemoryConfig) -> EpisodicMemory:
    global _instance
    if _instance is None:
        _instance = EpisodicMemory(cfg)
    return _instance


def get() -> EpisodicMemory | None:
    """The active memory service, or None if init() was never called."""
    return _instance
