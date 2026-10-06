"""One small sentence embedder for the whole brain, on the Pi's CPU. Pure zone.

Used by the entry classifier (routing/) and the facts memory (services/memory).
One model in RAM (~130 MB), loaded once: bge-small-en-v1.5 embeds a sentence in
~17 ms on the Pi 5 (measured 2026-10-07; all-MiniLM-L6-v2 is 8 ms but routed
far worse on the household's own utterances).

get() blocks while the model loads (~20 s from disk); callers on a turn path
use ready()/try_get() instead. Missing fastembed or model -> None, never a
raise (CLAUDE.md rule 5).
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

MODEL = os.environ.get("LANGROBO_EMBED_MODEL",
                       os.environ.get("LANGROBO_INTENT_MODEL", "BAAI/bge-small-en-v1.5"))
# Not fastembed's default (a temp dir): /tmp is wiped at boot and the model is
# ~130 MB to fetch again.
CACHE_DIR = os.path.expanduser(os.environ.get(
    "LANGROBO_EMBED_CACHE", "~/.cache/langrobo/fastembed"))

_model = None
_error: str | None = None
_load_s: float | None = None
_lock = threading.Lock()


def get():
    """The loaded model (fastembed TextEmbedding), or None if it cannot load."""
    global _model, _error, _load_s
    with _lock:
        if _model is not None or _error is not None:
            return _model
        t0 = time.monotonic()
        try:
            from fastembed import TextEmbedding
            os.makedirs(CACHE_DIR, exist_ok=True)
            _model = TextEmbedding(MODEL, cache_dir=CACHE_DIR)
            _load_s = round(time.monotonic() - t0, 1)
            logger.info("embedder ready (%s, %.1f s)", MODEL, _load_s)
        except Exception as e:
            _error = f"{type(e).__name__}: {e}"[:200]
            logger.warning("embedder unavailable: %s", _error)
        return _model


def start_loading() -> None:
    """Load in the background at boot, so no turn waits the ~20 s."""
    threading.Thread(target=get, daemon=True, name="embedder_load").start()


def try_get():
    """The model if it is already loaded; never waits for a load."""
    return _model


def embed(texts: list[str]):
    """list of vectors, or None when there is no model."""
    m = get()
    if m is None:
        return None
    return list(m.embed(list(texts)))


def status() -> dict:
    return {"model": MODEL, "ready": _model is not None, "error": _error, "load_s": _load_s}


def _reset_for_tests(model=None, error=None) -> None:
    global _model, _error
    with _lock:
        _model, _error = model, error
