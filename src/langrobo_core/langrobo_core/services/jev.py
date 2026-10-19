"""Jev — TypeSafe AI's "System One" decision model, for the brain's quick calls.

Jev (early access since 2026-09-15) does not write text: it answers typed
questions about a text "state" -- yes/no as a probability, or a pick from named
categories with a confidence -- in one fast, cheap cloud call. So it does the
brain's small DECISIONS, never its talking, seeing or driving:

  route   which agent should take this turn (chat / local_agent / navigate).
          Today chat spends a whole 12B LLM call deciding to hand over -- the
          first move or vision question costs 3 calls, and each call re-reads a
          ~2.5k-token prompt (1-2 s warm, 15-27 s cold). A confident pick here
          enters the right agent directly: 2 calls.
  vision  is the user asking about what the camera sees? Backs up the
          vision-question regex in graph/build.py, which only knows the
          phrasings someone wrote down.
  done    has the speaker finished the request? utils/utterance.looks_incomplete
          is a word list; when it says "wait for more", a confident "finished"
          here saves the merge window.

NOT for: stop words or anything safety-related (the wheels stop on every
utterance with no model at all, and must keep doing so), distances and angles
(Jev is weak with numbers; move_robot's parser is exact), or images (text only).

MODES (LANGROBO_JEV):
  off     (default) never called.
  shadow  asked in the background; changes nothing. Each turn logs Jev's pick
          beside what the brain actually did, and /status keeps the running
          agreement -- measure it on your own conversations before trusting it.
  on      acted on, only at confidence >= LANGROBO_JEV_MIN_CONF (0.9); below
          that, or on no key / no internet / a timeout, today's path runs.
          Missing keys degrade, never crash (CLAUDE.md rule 5).

PRIVACY: in shadow and on modes the user's words (and the robot's previous
reply, for context) go to api.typesafe.ai.

Pure: no ROS. agent_node calls read_turn(); the graph reads the result from
state. API shape from TypeSafe's reference client (simonw/llm-typesafe).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

API_URL = os.environ.get("LANGROBO_JEV_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("LANGROBO_JEV_MODEL", "jev-latest")

_client = None
_client_lock = threading.Lock()
_stats_lock = threading.Lock()
stats = {"calls": 0, "errors": 0, "timeouts": 0, "latency_ms_total": 0,
         "shadow_turns": 0, "agree": 0, "disagree": 0,
         "confident": 0, "confident_agree": 0, "acted": 0}


def mode() -> str:
    """off | shadow | on. No API key is always off."""
    m = os.environ.get("LANGROBO_JEV", "off").strip().lower()
    if m not in ("shadow", "on"):
        return "off"
    return m if api_key() else "off"


def api_key() -> str:
    return os.environ.get("TYPESAFE_API_KEY", "").strip()


def min_conf() -> float:
    try:
        return float(os.environ.get("LANGROBO_JEV_MIN_CONF", "0.9"))
    except ValueError:
        return 0.9


def timeout_s() -> float:
    try:
        return float(os.environ.get("LANGROBO_JEV_TIMEOUT_S", "1.5"))
    except ValueError:
        return 1.5


def _http():
    """One client, kept alive: a new TLS handshake per turn would cost more
    than the answer."""
    global _client
    with _client_lock:
        if _client is None:
            import httpx
            _client = httpx.Client(timeout=timeout_s(), follow_redirects=False)
        return _client


def _count(**inc) -> None:
    with _stats_lock:
        for k, v in inc.items():
            stats[k] += v


def ask(state, questions: dict, timeout: float | None = None) -> dict | None:
    """One request, several questions. {question_id: answer} or None on any
    failure -- never raises. `state` is a string, dict or list."""
    if not api_key():
        return None
    payload = {"model": MODEL, "state": state, "questions": questions}
    t0 = time.monotonic()
    try:
        r = _http().post(API_URL, json=payload,
                         headers={"Authorization": f"Bearer {api_key()}"},
                         timeout=timeout if timeout is not None else timeout_s())
        ms = int((time.monotonic() - t0) * 1000)
        _count(calls=1, latency_ms_total=ms)
        if r.status_code != 200:
            _count(errors=1)
            logger.warning("jev: HTTP %s: %s", r.status_code, r.text[:200])
            return None
        answers = r.json().get("answers")
        return answers if isinstance(answers, dict) else None
    except Exception as e:
        is_timeout = "Timeout" in type(e).__name__
        _count(calls=1, timeouts=int(is_timeout), errors=int(not is_timeout))
        logger.warning("jev: %s: %s", type(e).__name__, e)
        return None


# ── The per-turn read ───────────────────────────────────────────────────────

_ROUTE_Q = ("Which assistant should handle the user's utterance? The robot has a "
            "camera and wheels. Follow-ups ('is it still there?', 'now turn left', "
            "'what colour was it?') belong with the previous assistant's topic.")
_VISION_Q = ("Is the user asking about what the robot's camera sees right now, or "
             "saw a moment ago (describing the scene, whether something or someone "
             "is visible, what something looks like)?")
_DONE_Q = ("Is this a complete request the robot can act on or answer now, or did "
           "the speaker stop mid-sentence and will keep talking?")


@dataclass(frozen=True)
class TurnRead:
    route: str | None          # the agent Jev picked
    route_conf: float          # its confidence, 0..1
    vision: float | None       # P(the user asks about what the camera sees)
    latency_ms: int

    def confident_route(self, floor: float | None = None) -> str | None:
        f = min_conf() if floor is None else floor
        return self.route if self.route and self.route_conf >= f else None


def _state(text: str, previous_agent: str | None, previous_reply: str | None) -> dict:
    return {"utterance": text,
            "previous_assistant": previous_agent or "none",
            "previous_reply": (previous_reply or "")[:300]}


def read_turn(text: str, agents: dict, previous_agent: str | None = None,
              previous_reply: str | None = None,
              timeout: float | None = None) -> TurnRead | None:
    """Route + vision for one user turn, in ONE request. `agents` is
    registry.AGENTS ({name: {"description": ...}}) -- the same routing copy
    chat routes on, so the two cannot drift. None on any failure."""
    if not text or not text.strip() or not agents:
        return None
    t0 = time.monotonic()
    ans = ask(_state(text, previous_agent, previous_reply), {
        "route": {"type": "choice", "instructions": _ROUTE_Q,
                  "criteria": {name: meta["description"] for name, meta in agents.items()}},
        "vision": {"type": "noul", "instructions": _VISION_Q},
    }, timeout)
    if not ans:
        return None
    route = ans.get("route") or {}
    pick = route.get("choice")
    try:
        conf = float(route.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    try:
        vision = float((ans.get("vision") or {}).get("noul"))
    except (TypeError, ValueError):
        vision = None
    return TurnRead(route=pick if pick in agents else None, route_conf=conf,
                    vision=vision, latency_ms=int((time.monotonic() - t0) * 1000))


def entry_for(read: TurnRead | None, default_entry: str) -> tuple[str, bool]:
    """(the agent to enter, whether Jev decided it) for LANGROBO_JEV=on.
    A confident pick wins; anything else is today's rule (sticky, else chat)."""
    pick = read.confident_route() if read is not None else None
    if pick and pick != default_entry:
        _count(acted=1)
        return pick, True
    return default_entry, False


def is_finished(text: str, timeout: float | None = None) -> float | None:
    """P(the utterance is a complete request). None on failure."""
    ans = ask({"utterance": text}, {
        "done": {"type": "noul", "instructions": _DONE_Q}}, timeout)
    try:
        return float((ans or {}).get("done", {}).get("noul"))
    except (TypeError, ValueError):
        return None


def record_outcome(read: TurnRead | None, actual_agent: str | None,
                   shadow: bool) -> dict | None:
    """Compare Jev's pick with the agent that actually answered the turn;
    keeps the running agreement for /status. Returns the log fields."""
    if read is None or not actual_agent:
        return None
    agree = read.route == actual_agent
    confident = read.confident_route() is not None
    _count(shadow_turns=int(shadow), agree=int(agree), disagree=int(not agree),
           confident=int(confident), confident_agree=int(confident and agree))
    return {"jev_route": read.route, "jev_conf": round(read.route_conf, 3),
            "jev_vision": None if read.vision is None else round(read.vision, 3),
            "actual": actual_agent, "agree": agree, "confident": confident,
            "latency_ms": read.latency_ms}


def status() -> dict:
    with _stats_lock:
        s = dict(stats)
    judged = s["agree"] + s["disagree"]
    return {"mode": mode(), "min_conf": min_conf(), **s,
            "agreement": round(s["agree"] / judged, 3) if judged else None,
            "confident_agreement": (round(s["confident_agree"] / s["confident"], 3)
                                    if s["confident"] else None),
            "latency_ms_avg": (round(s["latency_ms_total"] / s["calls"])
                               if s["calls"] else None)}


def log_line(fields: dict) -> str:
    return "jev " + json.dumps(fields, separators=(",", ":"))
