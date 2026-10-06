"""Facts memory -- what the household TELLS the robot, kept across reboots. Pure zone.

Owner, 2026-10-07: "remembering all". The robot's memory of what it SAW is its
photos (tools/photos.py); this is the other half, what it was TOLD: "Amma takes
her BP pills at 9", "Rakesh likes filter coffee", "the spare keys are in the
blue drawer". Qdrant episodic memory was cut on 2026-09-06 as real weight; this
is the small version: one SQLite file, no server, ~17 ms to embed on the Pi.

  remember  adds a fact (an exact repeat only refreshes its date). Nothing is
            ever overwritten by similarity -- "Amma likes tea" and "Dad likes
            tea" embed almost alike. A changed fact is forget + remember.
  recall    the best matches, newest first among equals, with who said it and
            when -- so the model can tell the current fact from an old one.
  forget    removes the ONE best match, and only a clear one.

Search is by meaning (services/embedder); with no embedder it falls back to
word overlap, so recall still works on a Pi without fastembed (rule 5).

Retrieval is TOOL-DRIVEN (CLAUDE.md rule 3): nothing from here is put into a
system prompt, so no KV cache is touched by a new fact.

State: <state dir>/memory.db (LANGROBO_STATE_DIR; the twin keeps its own).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time

import numpy as np

from . import embedder
from .config import state_path

MAX_FACTS = 2000
RECALL_K = 5
# Cosine floors for bge-small (its scores sit high: unrelated ~0.45-0.6).
RECALL_FLOOR = 0.62
FORGET_FLOOR = 0.80
FORGET_MARGIN = 0.03
WORD_FLOOR = 0.34          # share of the query's content words, no embedder

_lock = threading.Lock()
_STOP = frozenset("a an the is are was were be to of in on at for and or my your our "
                  "his her their it this that what where when who how do does did "
                  "i me you we they he she remember recall tell about".split())


def _db() -> sqlite3.Connection:
    import os
    path = state_path("memory.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY, text TEXT NOT NULL, norm TEXT NOT NULL,
        who TEXT, created REAL NOT NULL, updated REAL NOT NULL, vec BLOB)""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS facts_norm ON facts(norm)")
    return con


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (text or "").lower())).strip()


def _words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if w not in _STOP}


def _vec(text: str):
    got = embedder.embed([text])
    if not got:
        return None
    v = np.asarray(got[0], dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else None


def remember(text: str, who: str | None = None, now: float | None = None) -> tuple[str, str]:
    """("added" | "refreshed", the fact as stored)."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    norm = _norm(text)
    if not norm:
        raise ValueError("empty fact")
    now = time.time() if now is None else now
    v = _vec(text)
    with _lock, _db() as con:
        row = con.execute("SELECT id FROM facts WHERE norm=?", (norm,)).fetchone()
        if row:
            con.execute("UPDATE facts SET updated=?, who=COALESCE(?, who) WHERE id=?",
                        (now, who, row[0]))
            return "refreshed", text
        con.execute("INSERT INTO facts(text, norm, who, created, updated, vec) "
                    "VALUES (?,?,?,?,?,?)",
                    (text, norm, who, now, now, v.tobytes() if v is not None else None))
        n = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        if n > MAX_FACTS:                       # oldest-touched goes first
            con.execute("DELETE FROM facts WHERE id IN (SELECT id FROM facts "
                        "ORDER BY updated LIMIT ?)", (n - MAX_FACTS,))
    return "added", text


def _scored(query: str) -> tuple[list[tuple[float, dict]], bool]:
    """([(score, fact)], whether the scores are by meaning or by word overlap)."""
    with _lock, _db() as con:
        rows = con.execute("SELECT id, text, who, created, updated, vec FROM facts").fetchall()
    if not rows:
        return [], False
    facts = [{"id": r[0], "text": r[1], "who": r[2], "created": r[3], "updated": r[4]}
             for r in rows]
    q = _vec(query)
    if q is not None:
        missing = [i for i, r in enumerate(rows) if r[5] is None]
        if missing:                           # stored while the embedder was down
            _backfill([facts[i] for i in missing])
            with _lock, _db() as con:
                rows = con.execute("SELECT id, text, who, created, updated, vec "
                                   "FROM facts").fetchall()
        mats = [np.frombuffer(r[5], dtype=np.float32) if r[5] else None for r in rows]
        scores = [float(m @ q) if m is not None and m.shape == q.shape else 0.0
                  for m in mats]
        return list(zip(scores, facts)), True
    qw = _words(query)                         # no embedder: word overlap
    return [(len(qw & _words(f["text"])) / max(len(qw), 1), f) for f in facts], False


def _backfill(facts: list[dict]) -> None:
    vecs = embedder.embed([f["text"] for f in facts])
    if not vecs:
        return
    with _lock, _db() as con:
        for f, v in zip(facts, vecs):
            v = np.asarray(v, dtype=np.float32)
            v = v / (float(np.linalg.norm(v)) or 1.0)
            con.execute("UPDATE facts SET vec=? WHERE id=?", (v.tobytes(), f["id"]))


def recall(query: str, k: int = RECALL_K) -> list[dict]:
    """The facts that match `query`, best first (newest first among near-equals)."""
    if not (query or "").strip():
        return []
    scored, semantic = _scored(query)
    floor = RECALL_FLOOR if semantic else WORD_FLOOR
    hits = [(s, f) for s, f in scored if s >= floor]
    hits.sort(key=lambda sf: (-round(sf[0], 2), -sf[1]["updated"]))
    return [{**f, "score": round(s, 3)} for s, f in hits[:k]]


def forget(query: str) -> dict | None:
    """Delete the single clear best match; the deleted fact, or None."""
    scored, semantic = _scored(query)
    scored.sort(key=lambda sf: -sf[0])
    if not scored:
        return None
    best, fact = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if _norm(query) != _norm(fact["text"]):
        floor = FORGET_FLOOR if semantic else 0.6
        if best < floor or best - second < FORGET_MARGIN:
            return None
    with _lock, _db() as con:
        con.execute("DELETE FROM facts WHERE id=?", (fact["id"],))
    return fact


def count() -> int:
    with _lock, _db() as con:
        return con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
