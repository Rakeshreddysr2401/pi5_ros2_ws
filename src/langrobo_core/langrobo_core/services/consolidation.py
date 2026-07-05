"""Nightly memory consolidation — episodic turns → durable facts. Pure zone.

This is the robot's "self-learning" (PRODUCT.md v1.1, PRD §6): while the
house sleeps, recent conversation episodes are distilled by the LOCAL model
into short household facts ("Rakesh likes his coffee black") stored in the
`facts` Qdrant collection. recall_memory() then surfaces them for months,
long after the raw turns have scrolled out of any context window. Nothing
ever leaves the device — that is the product's moat, so a cloud fallback is
deliberately NOT used here.

How it runs:
  - agent_node's consolidation timer calls maybe_run() once a minute; it
    fires at most once per day, at/after the configured hour, only while the
    robot is idle and the local LLM is reachable.
  - Episodes newer than the persisted cursor are read in batches; each batch
    is one LLM call (prompts.CONSOLIDATION_PROMPT → strict JSON array).
  - Facts are deduplicated by embedding similarity (memory.store_fact), so
    re-processing the same episodes is harmless.
  - The run aborts between batches the moment the robot has real work
    (should_abort callback) and resumes later the same day — the cursor only
    advances past episodes that were actually processed.
  - LLM calls ride the SPECIALIST llama.cpp slot (agent_node registers a
    "consolidation" override), so a 3am run never evicts chat's hot prefix.

Design constraints (same contract as the other services): never crash the
brain, never block a turn, degrade to "skipped" with an honest log line.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import llm as llm_module
from . import metrics
from .config import ConsolidationConfig

logger = logging.getLogger(__name__)

_FAILURE_BACKOFF_S = 1800   # after a failed run, don't retry for 30 min


class Consolidator:

    def __init__(self, cfg: ConsolidationConfig, memory):
        self._cfg = cfg
        self._memory = memory
        self._path = os.path.expanduser(cfg.state_path)
        self._state = self._load()
        self._lock = threading.Lock()
        self._running = False
        self._retry_after = 0.0     # monotonic; in-memory backoff after failures
        self._last_result: str | None = None

    # ── Scheduling (agent_node timer, spin thread) ─────────────────────────

    def due(self, now: float | None = None) -> bool:
        """True when a run should start: enabled, memory up, local LLM up,
        at/after the nightly hour, not yet completed today, not backing off."""
        if not self._cfg.enabled or self._running:
            return False
        if not self._memory.available() or not llm_module.primary_available():
            return False
        if time.monotonic() < self._retry_after:
            return False
        lt = time.localtime(now)
        if lt.tm_hour < self._cfg.hour:
            return False
        return self._state.get("last_day") != time.strftime("%Y-%m-%d", lt)

    def maybe_run(self, should_abort) -> None:
        """Spawn one background run if due. `should_abort()` is checked between
        batches — the robot's real work always wins over housekeeping."""
        if not self.due():
            return
        self._running = True
        threading.Thread(target=self._run_guarded, args=(should_abort,),
                         daemon=True, name="consolidation").start()

    def _run_guarded(self, should_abort) -> None:
        try:
            self.run_once(should_abort)
        except Exception as e:
            # Belt and braces — run_once handles its own errors; this catches
            # bugs in the handler itself so the thread never dies loudly.
            self._retry_after = time.monotonic() + _FAILURE_BACKOFF_S
            self._last_result = f"crashed: {type(e).__name__}: {e}"
            logger.exception("consolidation run crashed")
        finally:
            self._running = False

    # ── The run (background thread) ────────────────────────────────────────

    def run_once(self, should_abort=lambda: False) -> dict:
        """One consolidation pass. Returns a summary dict (also logged)."""
        started = time.time()
        cursor = float(self._state.get("last_ts", 0.0))
        episodes = self._memory.episodes_since(cursor, limit=self._cfg.max_episodes)

        if len(episodes) < self._cfg.min_episodes:
            # Nothing worth a model call — mark today done so the timer stops
            # asking until tomorrow.
            self._state["last_day"] = time.strftime("%Y-%m-%d")
            self._save()
            self._last_result = f"skipped ({len(episodes)} new episodes)"
            logger.info("Consolidation skipped — only %d new episode(s)", len(episodes))
            return {"episodes": len(episodes), "stored": 0, "skipped": True}

        stored = duplicates = 0
        processed_ts = cursor
        aborted = False
        llm = llm_module.get_llm("consolidation")

        for i in range(0, len(episodes), self._cfg.batch_size):
            if should_abort():
                aborted = True
                break
            batch = episodes[i:i + self._cfg.batch_size]
            try:
                facts = self._distill(llm, batch)
            except Exception as e:
                if llm_module.is_connection_error(e):
                    llm_module.report_primary_failure()
                self._retry_after = time.monotonic() + _FAILURE_BACKOFF_S
                self._last_result = f"failed: {type(e).__name__}"
                logger.warning("Consolidation batch failed (%s) — will retry later; "
                               "cursor kept at processed point", type(e).__name__)
                aborted = True
                break
            for fact in facts:
                if self._memory.store_fact(fact):
                    stored += 1
                else:
                    duplicates += 1
            processed_ts = max(processed_ts,
                               max(ep.get("ts", 0.0) for ep in batch))

        # Advance the cursor only past what was actually processed; mark the
        # day complete only if everything was.
        self._state["last_ts"] = processed_ts
        if not aborted:
            self._state["last_day"] = time.strftime("%Y-%m-%d")
            self._state["runs"] = self._state.get("runs", 0) + 1
            self._state["facts_total"] = self._state.get("facts_total", 0) + stored
        self._save()

        metrics.inc("consolidation_runs_total")
        summary = {"episodes": len(episodes), "stored": stored,
                   "duplicates": duplicates, "aborted": aborted,
                   "seconds": round(time.time() - started, 1)}
        self._last_result = ("aborted" if aborted else "ok") + f" — {summary}"
        logger.info("Consolidation %s: %d episode(s) → %d new fact(s), "
                    "%d duplicate(s), %.1fs",
                    "aborted early" if aborted else "complete",
                    len(episodes), stored, duplicates, summary["seconds"])
        return summary

    def _distill(self, llm, batch: list[dict]) -> list[str]:
        """One LLM call: numbered episode list in, JSON array of facts out."""
        from langchain_core.messages import HumanMessage, SystemMessage
        from ..prompts import CONSOLIDATION_PROMPT
        lines = []
        for n, ep in enumerate(batch, 1):
            who = f" ({ep['person']})" if ep.get("person") else ""
            lines.append(f"{n}. User{who}: {ep.get('user', '')}\n"
                         f"   Robot: {ep.get('robot', '')}")
        reply = llm.invoke([SystemMessage(content=CONSOLIDATION_PROMPT),
                            HumanMessage(content="\n".join(lines))])
        return self._parse_facts(reply.content)

    @staticmethod
    def _parse_facts(text) -> list[str]:
        """Parse the model's JSON array, tolerating markdown fences and prose
        around it — local models decorate despite 'strict JSON'."""
        if isinstance(text, list):   # multimodal content blocks
            text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
        match = re.search(r"\[.*\]", text or "", re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return []
        return [f.strip() for f in parsed
                if isinstance(f, str) and f.strip()][:50]

    # ── Health surface ──────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "enabled": self._cfg.enabled,
            "running": self._running,
            "last_day": self._state.get("last_day"),
            "runs": self._state.get("runs", 0),
            "facts_total": self._state.get("facts_total", 0),
            "last_result": self._last_result,
        }

    # ── Persistence (write-then-rename, same as the reminder store) ────────

    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                return dict(json.load(f))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._state, f, indent=1)
            os.replace(tmp, self._path)
        except OSError:
            logger.exception("failed to persist consolidation state")


# Module-level singleton — same pattern as the other services.
_instance: Consolidator | None = None


def init(cfg: ConsolidationConfig, memory) -> Consolidator:
    global _instance
    if _instance is None:
        _instance = Consolidator(cfg, memory)
    return _instance


def get() -> Consolidator | None:
    return _instance
