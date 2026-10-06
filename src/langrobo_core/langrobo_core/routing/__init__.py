"""Entry routing on the Pi -- which agent a user turn enters, with no LLM call.

LANGROBO_INTENT_ROUTING:
  off     (default) never loaded.
  shadow  classifies every user turn and logs the pick beside the agent that
          actually answered; changes nothing. /status keeps the agreement.
  on      a confident pick enters that agent directly; no pick -> today's rule
          (the sticky agent, else chat).

The model loads on a background thread at boot (~20 s on the Pi 5); until it
is ready, and if fastembed or the model is missing, every turn routes exactly
as it does without this (CLAUDE.md rule 5). [SYSTEM] turns are never
classified.

intent.py is the pure classifier; this module owns the model, the mode and the
counters.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from ..services import embedder
from .intent import IntentRouter, Pick, entry_for, strip_address  # noqa: F401

logger = logging.getLogger(__name__)


_router: IntentRouter | None = None
_state = {"loading": False, "error": None, "load_s": None}
_lock = threading.Lock()
stats = {"turns": 0, "picked": 0, "acted": 0, "judged": 0, "agree": 0,
         "picked_judged": 0, "picked_agree": 0, "ms_total": 0}


def mode() -> str:
    m = os.environ.get("LANGROBO_INTENT_ROUTING", "off").strip().lower()
    return m if m in ("shadow", "on") else "off"


def examples() -> dict[str, list[str]]:
    from ..registry import SPECS
    return {name: list(spec.examples) + list(spec.intent_examples)
            for name, spec in SPECS.items()}


def start_loading() -> None:
    """Load the embedder on a daemon thread. Safe to call more than once."""
    if mode() == "off":
        return
    with _lock:
        if _router is not None or _state["loading"]:
            return
        _state["loading"] = True
    threading.Thread(target=_load, daemon=True, name="intent_router_load").start()


def _load() -> None:
    global _router
    t0 = time.monotonic()
    try:
        model = embedder.get()
        if model is None:
            raise RuntimeError(embedder.status()["error"] or "no embedder")
        router = IntentRouter(examples(), lambda xs: list(model.embed(xs)))
        router.classify("warm up")
        with _lock:
            _router = router
            _state["load_s"] = round(time.monotonic() - t0, 1)
        logger.info("intent router ready (%.1f s)", _state["load_s"])
    except Exception as e:                       # missing package/model: degrade
        _state["error"] = f"{type(e).__name__}: {e}"[:200]
        logger.warning("intent router unavailable, routing as before: %s", _state["error"])
    finally:
        _state["loading"] = False


def ready() -> bool:
    return _router is not None


def classify(text: str) -> Pick | None:
    """The pick for one user utterance, or None when off / not ready / failed."""
    router = _router
    if router is None or mode() == "off":
        return None
    t0 = time.monotonic()
    try:
        pick = router.classify(text)
    except Exception as e:
        logger.warning("intent router failed on a turn: %s", e)
        return None
    ms = int((time.monotonic() - t0) * 1000)
    _count(turns=1, picked=int(pick.agent is not None), ms_total=ms)
    return pick


def record_outcome(pick: Pick | None, actual_agent: str | None, acted: bool) -> dict | None:
    """Compare the pick with the agent that answered; the log fields, or None."""
    if pick is None or not actual_agent:
        return None
    agree = (pick.agent or pick.nearest) == actual_agent
    _count(acted=int(acted), judged=1, agree=int(agree),
           picked_judged=int(pick.agent is not None),
           picked_agree=int(pick.agent is not None and agree))
    return {"pick": pick.agent, "nearest": pick.nearest, "score": round(pick.score, 3),
            "margin": round(pick.margin, 3), "actual": actual_agent, "acted": acted}


def _count(**inc) -> None:
    with _lock:
        for k, v in inc.items():
            stats[k] += v


def status() -> dict:
    with _lock:
        s = dict(stats)
    return {"mode": mode(), "model": embedder.MODEL, "ready": ready(), **_state, **s,
            "picked_precision": (round(s["picked_agree"] / s["picked_judged"], 3)
                                 if s["picked_judged"] else None),
            "ms_avg": round(s["ms_total"] / s["turns"], 1) if s["turns"] else None}


def log_line(fields: dict) -> str:
    return "intent " + json.dumps(fields, separators=(",", ":"))
