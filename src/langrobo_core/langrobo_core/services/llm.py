"""LLM factory + fallback policy — configured once at startup.

A single get_llm() factory draws from a module-level config dict so every
agent gets the same settings without passing LLM objects around.

Per-agent overrides: get_llm(agent_name) merges the global config with an
optional per-agent override dict. This lets one agent (e.g. "local_agent")
pin a dedicated llama.cpp KV-cache slot via `slot`, or run on a different
provider/model entirely — without disturbing the others. See agent_params.yaml.

Fallback policy (local-first): the primary (Mac Mini llama.cpp) is always
preferred. When a call fails with a connection-class error, the primary is
marked down for a cooldown window and — if a cloud fallback is configured via
LANGROBO_FALLBACK_* — calls go straight to the fallback until the window
expires (then the primary is re-probed). With no fallback configured the
robot speaks a degraded "my brain is offline" message instead of dying.
safe_invoke() in utils.message_utils drives this policy per call.
"""

from __future__ import annotations

import logging
import threading
import time

from . import metrics
from .config import FallbackLLM

logger = logging.getLogger(__name__)

_config: dict = {}
_agent_overrides: dict = {}
_fallback: FallbackLLM | None = None

# Primary-health state: after a connection failure the primary is skipped
# until this monotonic deadline, then re-probed by the next call.
_health_lock = threading.Lock()
_primary_down_until: float = 0.0
PRIMARY_COOLDOWN_S = 60.0


def configure(
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    max_tokens: int,
    agent_overrides: dict | None = None,
    streaming: bool = False,
    slot: int | None = None,
) -> None:
    """Called once by the entry point before the graph is built.

    agent_overrides: {agent_name: {provider?, model?, base_url?, api_key?,
                      max_tokens?, slot?, streaming?}} — any subset of fields
                      overrides the global config for that agent only.
    streaming:       when True, invoke() streams under the hood and fires
                     on_llm_new_token callbacks — this is what feeds sentence
                     chunks to TTS (utils.speech_stream). openai/llamacpp
                     providers only.
    slot:            default llama.cpp KV-cache slot for ALL agents (id_slot).
                     Sequential requests land on the same server slot, so the
                     shared static prompt prefix stays cached — without this a
                     multi-slot server (--parallel N) scatters requests across
                     cold slots and re-prefills the whole prompt (~20s on a
                     12B model). Per-agent `slot` overrides still win
                     (e.g. local_agent's image cache slot). None/-1 = no pin.
    """
    global _config, _agent_overrides
    _config = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "streaming": streaming,
        "slot": slot,
    }
    _agent_overrides = agent_overrides or {}


def configure_fallback(fallback: FallbackLLM) -> None:
    """Install the cloud fallback (from services.config.load_settings()).

    The provider's package is BUILT once here, at startup, and the fallback is
    disarmed if that fails. It has to be checked now rather than on first use,
    because `safe_invoke` asks for the fallback object BEFORE it tries the
    primary — so an unimportable provider does not merely break the fallback,
    it raises on every LLM call the robot ever makes, including the ones where
    the primary is perfectly healthy.

    That is reachable in normal operation: anthropic/gemini/ollama are optional
    extras (pyproject.toml), so `pip install -r requirements.txt` on the Pi 5
    installs none of them. Configuring a fallback you have not installed is a
    plausible mistake, and rule 4 in CLAUDE.md says it must degrade, not crash.
    """
    global _fallback
    _fallback = fallback if fallback.configured else None
    if not _fallback:
        return
    try:
        _build({
            "provider": _fallback.provider, "model": _fallback.model,
            "base_url": _fallback.base_url, "api_key": _fallback.api_key or "none",
            "max_tokens": 16, "streaming": False, "slot": None,
        })
    except Exception as e:
        logger.error(
            "LLM fallback %s/%s is configured but cannot be built (%s) — "
            "DISARMED. The robot will speak an offline message instead of "
            "falling back. Install the extra: pip install 'langrobo-core[%s]'",
            _fallback.provider, _fallback.model, e, _fallback.provider)
        _fallback = None
        return
    logger.info("LLM fallback armed: %s/%s", _fallback.provider, _fallback.model)


# ── Primary health tracking (drives safe_invoke's routing) ─────────────────

def primary_available() -> bool:
    """False while the primary is inside its post-failure cooldown window."""
    with _health_lock:
        return time.monotonic() >= _primary_down_until


def report_primary_failure() -> None:
    """Mark the primary down for PRIMARY_COOLDOWN_S (connection-class errors only)."""
    global _primary_down_until
    with _health_lock:
        _primary_down_until = time.monotonic() + PRIMARY_COOLDOWN_S
    metrics.inc("llm_primary_failures_total")
    metrics.set_gauge("llm_primary_up", 0)
    logger.warning("Primary LLM marked down for %.0fs", PRIMARY_COOLDOWN_S)


def report_primary_success() -> None:
    global _primary_down_until
    with _health_lock:
        _primary_down_until = 0.0
    metrics.set_gauge("llm_primary_up", 1)


def is_connection_error(exc: BaseException) -> bool:
    """Heuristic: does this exception mean 'server unreachable / dead', as
    opposed to a request-shaped error the fallback would hit too?"""
    names = {t.__name__ for t in type(exc).__mro__}
    wanted = {
        "APIConnectionError", "APITimeoutError", "InternalServerError",
        "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
        "PoolTimeout", "TimeoutException", "ConnectionError", "OSError",
    }
    if names & wanted:
        return True
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        return is_connection_error(cause)
    return False


def status() -> dict:
    """Health-API view of the LLM layer."""
    with _health_lock:
        down_for = max(0.0, _primary_down_until - time.monotonic())
    return {
        "provider": _config.get("provider", "unconfigured"),
        "base_url": _config.get("base_url", ""),
        "primary_available": down_for == 0.0,
        "primary_retry_in_s": round(down_for, 1),
        "fallback": f"{_fallback.provider}/{_fallback.model}" if _fallback else None,
    }


# ── Factories ───────────────────────────────────────────────────────────────

def get_llm(agent: str | None = None):
    """Return a fresh LLM instance for `agent` (or the global default).

    Merges the global config with any per-agent override.  For llama.cpp /
    openai providers, a non-negative `slot` is forwarded as `id_slot` so the
    server keeps that agent's KV cache in its own slot (no cross-agent eviction).
    """
    cfg = dict(_config)
    if agent and agent in _agent_overrides:
        cfg.update({k: v for k, v in _agent_overrides[agent].items() if v is not None})
    return _build(cfg)


def slot_for(agent: str | None) -> int | None:
    """The pinned llama.cpp slot `get_llm(agent)` would use, for log/diagnostic
    lines only (safe_invoke). Mirrors get_llm's own config merge without
    building a client — cheaper and avoids depending on langchain internals
    to read the slot back off a constructed LLM instance."""
    if agent and agent in _agent_overrides:
        override_slot = _agent_overrides[agent].get("slot")
        if override_slot is not None:
            return override_slot
    return _config.get("slot")


def get_fallback_llm():
    """The configured cloud fallback LLM, or None. Fresh instance per call.

    No slot pin (cloud servers have no KV slots) and no per-agent overrides —
    one fallback model serves every agent. Streaming mirrors the global flag:
    for openai-compatible fallbacks the sentence-streaming TTS path keeps
    working; other providers fall back to whole-reply speech (agent_node
    publishes the full text when nothing streamed).
    """
    if _fallback is None:
        return None
    try:
        return _build({
            "provider": _fallback.provider,
            "model": _fallback.model,
            "base_url": _fallback.base_url,
            "api_key": _fallback.api_key or "none",
            "max_tokens": _config.get("max_tokens", 6000),
            "streaming": _config.get("streaming", False),
            "slot": None,
        })
    except Exception as e:
        # configure_fallback already proved this builds, so reaching here means
        # something changed under us. Never let it kill a turn whose primary is
        # fine — safe_invoke asks for this before it tries the primary.
        logger.error("Fallback LLM could not be built (%s) — continuing without it", e)
        return None


def _build(cfg: dict):
    provider   = cfg.get("provider", "llamacpp")
    model      = cfg.get("model", "default")
    base_url   = cfg.get("base_url", "")
    api_key    = cfg.get("api_key", "none")
    max_tokens = cfg.get("max_tokens", 6000)
    slot       = cfg.get("slot")

    if provider in ("openai", "llamacpp"):
        from langchain_openai import ChatOpenAI
        kwargs: dict = {
            "model": model,
            "api_key": api_key,
            "temperature": 0,
            "max_tokens": max_tokens,
            # Retry policy lives in safe_invoke (one visible retry, then the
            # cooldown + fallback ladder). The SDK's hidden default of 2 more
            # internal retries stacked on top of that — a dead primary cost
            # ~6 connect attempts (~40s of silence) before the spoken offline
            # message instead of ~2.
            "max_retries": 0,
        }
        if base_url:
            kwargs["base_url"] = base_url
        # Pin a llama.cpp KV-cache slot for this agent (server needs --parallel N).
        # Forwarded verbatim into the request body via the OpenAI client's extra_body.
        if slot is not None and slot >= 0:
            kwargs["extra_body"] = {"id_slot": slot}
        if cfg.get("streaming"):
            # invoke() streams under the hood and fires on_llm_new_token so the
            # speech stream handler can chunk sentences to TTS mid-generation.
            # stream_usage keeps token counts in /diag/timing llm_end events.
            kwargs["streaming"] = True
            kwargs["stream_usage"] = True
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, api_key=api_key, max_tokens=max_tokens)

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, base_url=base_url or "http://localhost:11434")

    raise ValueError(
        f"Unknown provider: {provider!r}. "
        "Choose from: llamacpp | openai | anthropic | gemini | ollama"
    )
