"""Morning-briefing scheduler — once-daily [SYSTEM] producer state. Pure zone.

The briefing AGENT does the talking (agents/briefing.py); this only decides
WHEN: at/after LANGROBO_BRIEFING_HOUR, at most once per day, persisted so a
restart can't re-brief (~/.langrobo/briefing.json). Off unless the hour is
configured — a robot that starts talking at 8am unasked must be opted into.
agent_node's timer calls due()/mark_done() and injects the [SYSTEM] turn
(the standard proactive pattern; the supervisor routes it to `briefing`).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from .config import BriefingConfig

logger = logging.getLogger(__name__)


class BriefingScheduler:

    def __init__(self, cfg: BriefingConfig):
        self._cfg = cfg
        self._path = os.path.expanduser(cfg.state_path)
        self._lock = threading.Lock()
        self._state = self._load()

    def due(self, now: float | None = None) -> bool:
        if not self._cfg.enabled:
            return False
        lt = time.localtime(now)
        if lt.tm_hour < self._cfg.hour:
            return False
        with self._lock:
            return self._state.get("last_day") != time.strftime("%Y-%m-%d", lt)

    def mark_done(self) -> None:
        with self._lock:
            self._state["last_day"] = time.strftime("%Y-%m-%d")
            self._state["count"] = self._state.get("count", 0) + 1
            self._save()

    def status(self) -> dict:
        with self._lock:
            return {"enabled": self._cfg.enabled,
                    "hour": self._cfg.hour if self._cfg.enabled else None,
                    "last_day": self._state.get("last_day"),
                    "count": self._state.get("count", 0)}

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
            logger.exception("failed to persist briefing state")


_instance: BriefingScheduler | None = None


def init(cfg: BriefingConfig) -> BriefingScheduler:
    global _instance
    if _instance is None:
        _instance = BriefingScheduler(cfg)
        if cfg.enabled:
            logger.info("Morning briefing scheduled daily at %02d:00", cfg.hour)
    return _instance


def get() -> BriefingScheduler | None:
    return _instance
