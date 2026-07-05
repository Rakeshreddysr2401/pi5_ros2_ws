"""Home watch mode — armed person-detection alerts. Pure zone (no rclpy).

The product's first vision-TRIGGERED proactive feature (PRODUCT.md v1.2):
"someone in view while you're away → photo on your phone". No face
recognition exists yet, so alerting is explicit-arm ("watch the house") —
zero false alarms while people are home, and face-rec later upgrades this to
stranger-only without changing the plumbing.

How it runs (no new Jetson code):
  - tools/watch.py arms/disarms (voice or Telegram, capability-gated).
  - agent_node's watch poll timer keeps the Jetson target finder hunting the
    COCO class "person" (/vision/target) while armed and reads the cached
    /vision/target_result. This service owns the DECISION (armed? confident?
    outside cooldown?); the node owns the I/O wiring.
  - On alert: the photo goes to every owner-role Telegram member DIRECTLY
    (deterministic — a security alert must not depend on the LLM being up),
    then a "[SYSTEM] Watch alert" turn is enqueued so the robot also announces
    it aloud via the standard proactive-speech path.
  - Watch alerts deliberately BYPASS quiet hours: "someone is in your house
    at 3am" is exactly what quiet hours must not suppress.

Design constraints this file honors (same contract as services/telegram.py):
  - Armed state persists (~/.langrobo/watch.json) — a restart must not
    silently disarm the house.
  - Telegram unconfigured → arming still works (spoken announcements only)
    and the arm tool says so honestly. Nothing crashes.
  - Cooldown lives HERE (thread-safe), so one visitor is one alert, not 50.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import metrics
from .config import WatchConfig

logger = logging.getLogger(__name__)


class WatchService:
    """Armed-state store + alert decision + alert delivery. Thread-safe:
    the tool (worker thread) arms/disarms while the poll timer (spin thread)
    checks detections."""

    def __init__(self, cfg: WatchConfig):
        self._cfg = cfg
        self._path = os.path.expanduser(cfg.state_path)
        self._lock = threading.Lock()
        self._state = self._load()

    # ── Arm / disarm (tools/watch.py, worker thread) ───────────────────────

    def enabled(self) -> bool:
        return self._cfg.enabled

    def armed(self) -> bool:
        with self._lock:
            return bool(self._state.get("armed"))

    def arm(self, by: str) -> None:
        with self._lock:
            self._state.update({"armed": True, "armed_by": by, "armed_at": time.time()})
            self._save()
        metrics.set_gauge("watch_armed", 1)
        logger.info("Watch mode ARMED by %s", by)

    def disarm(self, by: str) -> None:
        with self._lock:
            self._state.update({"armed": False, "armed_by": by})
            self._save()
        metrics.set_gauge("watch_armed", 0)
        logger.info("Watch mode disarmed by %s", by)

    # ── Alert decision (agent_node poll timer, spin thread) ────────────────

    def should_alert(self, found: bool, confidence: float) -> bool:
        """True when an armed, confident detection falls outside the cooldown.
        Recording the alert time is part of the same atomic check, so two
        near-simultaneous detections can't both fire."""
        if not found or confidence < self._cfg.min_confidence:
            return False
        with self._lock:
            if not self._state.get("armed"):
                return False
            now = time.time()
            if now - self._state.get("last_alert_ts", 0.0) < self._cfg.cooldown_s:
                return False
            self._state["last_alert_ts"] = now
            self._state["alerts_sent"] = self._state.get("alerts_sent", 0) + 1
            self._save()
        return True

    # ── Alert delivery (background thread — never the spin thread) ─────────

    def send_alert(self, frame: bytes | None) -> list[str]:
        """Send the alert photo (or an honest no-camera text) to every
        owner-role Telegram member. Returns the names reached — empty when
        Telegram is unconfigured or every send failed (the caller still
        announces aloud; the alert must not vanish silently)."""
        from . import telegram as telegram_service
        svc = telegram_service.get()
        if svc is None or not svc.configured():
            logger.warning("Watch alert: Telegram unconfigured — spoken announcement only")
            return []
        stamp = time.strftime("%I:%M %p").lstrip("0")
        reached = []
        for member in svc.members_with_role("owner"):
            if frame is not None:
                err = svc.send_photo(member.chat_id, frame,
                                     caption=f"Watch alert — person seen at {stamp}")
            else:
                err = svc.send_message(member.chat_id,
                                       f"Watch alert — person seen at {stamp}, but the "
                                       f"camera feed is down so I have no photo.")
            if err:
                logger.warning("Watch alert to %s failed — %s", member.name, err)
            else:
                reached.append(member.name)
        metrics.inc("watch_alerts_total")
        logger.info("AUDIT watch alert delivered_to=%s frame=%s",
                    ",".join(reached) or "nobody", frame is not None)
        return reached

    # ── Health surface ──────────────────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self._cfg.enabled,
                "armed": bool(self._state.get("armed")),
                "armed_by": self._state.get("armed_by"),
                "armed_at": self._state.get("armed_at"),
                "last_alert_ts": self._state.get("last_alert_ts"),
                "alerts_sent": self._state.get("alerts_sent", 0),
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
            logger.exception("failed to persist watch state")


# Module-level singleton — same pattern as services.memory/telegram.
_instance: WatchService | None = None


def init(cfg: WatchConfig) -> WatchService:
    global _instance
    if _instance is None:
        _instance = WatchService(cfg)
        if _instance.armed():
            logger.info("Watch mode was armed before restart — still armed")
    return _instance


def get() -> WatchService | None:
    """The active watch service, or None if init() was never called."""
    return _instance
