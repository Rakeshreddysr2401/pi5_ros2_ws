"""Service-layer tests: config validation, LLM fallback policy
round-trip (skipped if qdrant/fastembed unavailable), metrics rendering."""


import pytest

from langrobo_core.services import config as config_service
from langrobo_core.services import metrics


# ── config ──────────────────────────────────────────────────────────────────

def test_defaults_load_clean(monkeypatch):
    for var in ("LANGROBO_FALLBACK_PROVIDER", "LANGROBO_HEALTH_PORT",
                "LANGROBO_API_TOKEN", "LANGROBO_HEALTH_HOST"):
        monkeypatch.delenv(var, raising=False)
    s = config_service.load_settings()
    assert not s.fallback.configured
    assert s.health.host == "127.0.0.1"     # tokenless → localhost only


def test_bad_port_fails_fast(monkeypatch):
    monkeypatch.setenv("LANGROBO_HEALTH_PORT", "not-a-port")
    with pytest.raises(config_service.ConfigError):
        config_service.load_settings()


def test_bad_fallback_provider_fails_fast(monkeypatch):
    monkeypatch.setenv("LANGROBO_FALLBACK_PROVIDER", "skynet")
    with pytest.raises(config_service.ConfigError):
        config_service.load_settings()


def test_lan_exposure_without_token_refused(monkeypatch):
    monkeypatch.delenv("LANGROBO_API_TOKEN", raising=False)
    monkeypatch.setenv("LANGROBO_HEALTH_HOST", "0.0.0.0")
    with pytest.raises(config_service.ConfigError):
        config_service.load_settings()


def test_fallback_arms_only_with_key(monkeypatch):
    monkeypatch.setenv("LANGROBO_FALLBACK_PROVIDER", "anthropic")
    monkeypatch.setenv("LANGROBO_FALLBACK_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("LANGROBO_FALLBACK_API_KEY_ENV", "TEST_FB_KEY")
    monkeypatch.delenv("TEST_FB_KEY", raising=False)
    assert not config_service.load_settings().fallback.configured
    monkeypatch.setenv("TEST_FB_KEY", "sk-test")
    assert config_service.load_settings().fallback.configured


def test_tracing_scrubbed_without_optin(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.delenv("LANGROBO_TRACING", raising=False)
    config_service.sanitize_tracing_env()
    import os
    assert "LANGCHAIN_TRACING_V2" not in os.environ


# ── llm fallback policy ─────────────────────────────────────────────────────

def test_primary_cooldown_cycle():
    from langrobo_core.services import llm
    llm.report_primary_success()
    assert llm.primary_available()
    llm.report_primary_failure()
    assert not llm.primary_available()
    llm.report_primary_success()
    assert llm.primary_available()


def test_connection_error_classification():
    from langrobo_core.services import llm
    assert llm.is_connection_error(ConnectionError("refused"))
    assert llm.is_connection_error(TimeoutError("slow"))     # OSError subclass
    assert not llm.is_connection_error(ValueError("bad request"))
    wrapped = RuntimeError("wrapper")
    wrapped.__cause__ = ConnectionError("refused")
    assert llm.is_connection_error(wrapped)


def test_safe_invoke_degrades_to_spoken_message():
    """Primary dead + no fallback → spoken offline message, never an exception."""
    import logging
    from langrobo_core.services import llm
    from langrobo_core.utils.message_utils import safe_invoke

    llm.configure("llamacpp", "default", "http://127.0.0.1:1", "none", 100)
    llm.configure_fallback(config_service.FallbackLLM("", "", configured=False))
    llm.report_primary_success()   # reset any cooldown from other tests

    class DeadLLM:
        def invoke(self, messages):
            raise ConnectionError("server down")

    msg = safe_invoke(DeadLLM(), [], logging.getLogger("test"), retries=0)
    assert "offline" in msg.content.lower()
    assert not llm.primary_available()   # cooldown armed
    llm.report_primary_success()


# ── metrics ─────────────────────────────────────────────────────────────────

def test_metrics_prometheus_render():
    metrics.inc("test_events_total", 2)
    metrics.set_gauge("test_gauge", 1.5)
    text = metrics.render_prometheus()
    assert "langrobo_test_events_total 2.0" in text
    assert "langrobo_test_gauge 1.5" in text
    assert "langrobo_uptime_seconds" in text





# ── Fallback that cannot be built must DISARM, not crash every turn ─────────
# anthropic/gemini/ollama are optional extras (pyproject.toml), so a stock
# `pip install -r requirements.txt` on the Pi 5 installs none of them.
# safe_invoke asks for the fallback object BEFORE it tries the primary, so an
# unimportable provider does not merely break the fallback — it raises on every
# LLM call the robot makes, including ones where the primary is healthy.

def _block_import(monkeypatch, prefix):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith(prefix):
            raise ImportError(f"No module named {prefix!r}")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)


def test_unbuildable_fallback_is_disarmed_at_startup(monkeypatch):
    from langrobo_core.services import llm as llm_service
    from langrobo_core.services.config import FallbackLLM

    _block_import(monkeypatch, "langchain_anthropic")
    llm_service.configure("llamacpp", "m", "http://127.0.0.1:9/v1", "none", 100)
    llm_service.configure_fallback(FallbackLLM(
        provider="anthropic", model="claude", api_key="k",
        base_url="", configured=True))

    assert llm_service.get_fallback_llm() is None
    assert llm_service.status()["fallback"] is None


def test_a_healthy_turn_survives_an_unbuildable_fallback(monkeypatch):
    """The regression this guards: the primary was fine and the turn still died."""
    import logging
    from langrobo_core.services import llm as llm_service
    from langrobo_core.services.config import FallbackLLM
    from langrobo_core.utils.message_utils import safe_invoke

    _block_import(monkeypatch, "langchain_anthropic")
    llm_service.configure("llamacpp", "m", "http://127.0.0.1:9/v1", "none", 100)
    llm_service.configure_fallback(FallbackLLM(
        provider="anthropic", model="claude", api_key="k",
        base_url="", configured=True))

    class _Primary:
        def invoke(self, messages):
            return "answered"

    assert safe_invoke(_Primary(), [], logging.getLogger(), agent="chat") == "answered"


def test_a_buildable_fallback_still_arms():
    """The guard must not disarm a fallback that is genuinely fine."""
    from langrobo_core.services import llm as llm_service
    from langrobo_core.services.config import FallbackLLM

    llm_service.configure("llamacpp", "m", "http://127.0.0.1:9/v1", "none", 100)
    llm_service.configure_fallback(FallbackLLM(
        provider="openai", model="gpt-4o-mini", api_key="k",
        base_url="", configured=True))
    assert llm_service.get_fallback_llm() is not None
    assert llm_service.status()["fallback"] == "openai/gpt-4o-mini"
