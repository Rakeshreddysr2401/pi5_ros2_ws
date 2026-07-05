"""Home watch mode + nightly memory consolidation — pure-zone tests.

Watch: arm/disarm persistence, the alert decision (confidence, cooldown,
armed gate), honest degrade without Telegram. Consolidation: strict-JSON
parsing of a chatty local model, a full run over a fake memory backend
(facts stored, cursor advanced, dedupe counted), the too-few-episodes skip,
and the abort-on-real-work path.
"""

import time
from types import SimpleNamespace

import pytest

from langrobo_core.services import config as config_service
from langrobo_core.services.config import ConsolidationConfig, WatchConfig
from langrobo_core.services.consolidation import Consolidator
from langrobo_core.services.watch import WatchService


# ── config ──────────────────────────────────────────────────────────────────

def test_watch_config_defaults(monkeypatch):
    for var in ("LANGROBO_WATCH", "LANGROBO_WATCH_COOLDOWN_S",
                "LANGROBO_WATCH_MIN_CONF", "LANGROBO_CONSOLIDATION",
                "LANGROBO_CONSOLIDATION_HOUR", "LANGROBO_HEALTH_HOST",
                "LANGROBO_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    s = config_service.load_settings()
    assert s.watch.enabled and s.watch.cooldown_s == 60
    assert s.consolidation.enabled and s.consolidation.hour == 3


def test_watch_bad_confidence_fails_fast(monkeypatch):
    monkeypatch.setenv("LANGROBO_WATCH_MIN_CONF", "1.5")
    with pytest.raises(config_service.ConfigError):
        config_service.load_settings()


# ── watch service ───────────────────────────────────────────────────────────

def _watch(tmp_path, **kw) -> WatchService:
    cfg = WatchConfig(enabled=True, state_path=str(tmp_path / "watch.json"),
                      cooldown_s=kw.pop("cooldown_s", 60),
                      min_confidence=kw.pop("min_confidence", 0.5))
    return WatchService(cfg)


def test_watch_arm_persists_across_restart(tmp_path):
    w = _watch(tmp_path)
    assert not w.armed()
    w.arm("Rakesh")
    # A brain restart must not silently disarm the house.
    w2 = _watch(tmp_path)
    assert w2.armed()
    assert w2.status()["armed_by"] == "Rakesh"
    w2.disarm("Rakesh")
    assert not _watch(tmp_path).armed()


def test_watch_alert_decision(tmp_path):
    w = _watch(tmp_path, cooldown_s=3600)
    # Unarmed → never alert, even on a perfect detection.
    assert not w.should_alert(found=True, confidence=0.99)
    w.arm("Rakesh")
    assert not w.should_alert(found=False, confidence=0.99)   # nothing found
    assert not w.should_alert(found=True, confidence=0.3)     # low confidence
    assert w.should_alert(found=True, confidence=0.9)         # first real hit
    # Cooldown: the same visitor is one alert, not fifty.
    assert not w.should_alert(found=True, confidence=0.9)
    assert w.status()["alerts_sent"] == 1


def test_watch_alert_without_telegram_degrades(tmp_path):
    w = _watch(tmp_path)
    w.arm("Rakesh")
    # No telegram service initialised in this process → empty reach list,
    # no exception (the caller still announces aloud).
    assert w.send_alert(frame=b"jpeg") == []


# ── consolidation: fact parsing ─────────────────────────────────────────────

def test_parse_facts_tolerates_decoration():
    parse = Consolidator._parse_facts
    assert parse('["A", "B"]') == ["A", "B"]
    assert parse('Here you go:\n```json\n["A"]\n```') == ["A"]
    assert parse("[]") == []
    assert parse("no json here") == []
    assert parse('[1, "ok", null, "  "]') == ["ok"]


# ── consolidation: full run over a fake backend ─────────────────────────────

class FakeMemory:
    def __init__(self, episodes):
        self._eps = episodes
        self.facts: list[str] = []

    def available(self):
        return True

    def episodes_since(self, ts, limit=200):
        return sorted((e for e in self._eps if e["ts"] > ts),
                      key=lambda e: e["ts"])[:limit]

    def store_fact(self, fact):
        if fact in self.facts:
            return False
        self.facts.append(fact)
        return True


def _episodes(n, t0=1000.0):
    return [{"user": f"note {i}", "robot": "Noted.", "ts": t0 + i, "person": None}
            for i in range(n)]


def _consolidator(tmp_path, memory, **kw) -> Consolidator:
    cfg = ConsolidationConfig(enabled=True,
                              state_path=str(tmp_path / "consol.json"),
                              min_episodes=kw.pop("min_episodes", 2),
                              max_episodes=kw.pop("max_episodes", 200),
                              batch_size=kw.pop("batch_size", 10))
    return Consolidator(cfg, memory)


def test_run_stores_facts_and_advances_cursor(tmp_path, monkeypatch):
    from langrobo_core.services import consolidation as consolidation_module
    mem = FakeMemory(_episodes(5))
    c = _consolidator(tmp_path, mem)
    fake_llm = SimpleNamespace(invoke=lambda msgs: SimpleNamespace(
        content='["Rakesh likes black coffee", "Rakesh likes black coffee"]'))
    monkeypatch.setattr(consolidation_module.llm_module, "get_llm",
                        lambda agent=None: fake_llm)

    summary = c.run_once()
    assert summary["stored"] == 1 and summary["duplicates"] == 1
    assert mem.facts == ["Rakesh likes black coffee"]
    assert not summary["aborted"]
    # Cursor advanced past everything → a rerun sees nothing new and skips.
    assert c.run_once()["skipped"]
    # Completed today → not due again until tomorrow.
    assert not c.due()


def test_run_skips_below_min_episodes(tmp_path, monkeypatch):
    mem = FakeMemory(_episodes(1))
    c = _consolidator(tmp_path, mem, min_episodes=5)
    summary = c.run_once()
    assert summary["skipped"] and mem.facts == []


def test_run_aborts_when_robot_has_work(tmp_path, monkeypatch):
    from langrobo_core.services import consolidation as consolidation_module
    mem = FakeMemory(_episodes(30))
    c = _consolidator(tmp_path, mem, batch_size=10)
    calls = []
    fake_llm = SimpleNamespace(invoke=lambda msgs: (calls.append(1),
                               SimpleNamespace(content=f'["fact {len(calls)}"]'))[1])
    monkeypatch.setattr(consolidation_module.llm_module, "get_llm",
                        lambda agent=None: fake_llm)

    # Abort after the first batch: the robot's real work wins.
    summary = c.run_once(should_abort=lambda: len(calls) >= 1)
    assert summary["aborted"] and len(calls) == 1
    # The cursor advanced only past the processed batch — the rest is
    # picked up by the next run, which completes.
    summary2 = c.run_once()
    assert not summary2["aborted"] and summary2["episodes"] == 20
