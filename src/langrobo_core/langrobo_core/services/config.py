"""Validated runtime configuration — the single place that reads the environment.

Two config sources, deliberately kept separate:
  - ROS parameters (agent_params.yaml)  → LLM provider/model/slots. Read by
    agent_node and passed into services.llm.configure(). Not this module's job.
  - Environment / .env                  → secrets and service settings (cloud
    fallback, Qdrant, health API, tracing). This module's job.

Rules:
  - Fail fast on *malformed* values (a bad port number is a deploy bug).
  - Degrade gracefully on *missing* optional keys (no Tavily key = no web
    search, not a crash) — each consumer logs its own "disabled" line once.
  - Never log secret values; log only which keys are present.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """A malformed setting that must be fixed before the robot runs."""


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}")
    if not lo <= val <= hi:
        raise ConfigError(f"{name} must be in [{lo}, {hi}], got {val}")
    return val


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


_VALID_PROVIDERS = ("llamacpp", "openai", "anthropic", "gemini", "ollama")


@dataclass(frozen=True)
class FallbackLLM:
    """Cloud fallback used when the primary (Mac Mini) LLM is unreachable."""
    provider: str
    model: str
    api_key: str = ""          # resolved value, never logged
    base_url: str = ""
    configured: bool = False   # True only when provider+model+key all present


@dataclass(frozen=True)
class MemoryConfig:
    """Episodic memory (Qdrant). Local embedded mode by default; set
    QDRANT_URL (+ optional QDRANT_API_KEY) to use a server / Qdrant Cloud."""
    enabled: bool
    path: str                  # local embedded store (used when url is empty)
    url: str = ""
    api_key: str = ""
    collection: str = "episodic"
    # Reserved for roadmap P4 (visual household memory) — same store, own collection.
    visual_collection: str = "visual"
    embed_model: str = "BAAI/bge-small-en-v1.5"   # fastembed ONNX, 384-dim


@dataclass(frozen=True)
class HealthConfig:
    """In-process health/status/metrics API (FastAPI)."""
    enabled: bool
    host: str
    port: int
    token: str = ""            # bearer token; empty → API binds localhost only


@dataclass(frozen=True)
class Settings:
    fallback: FallbackLLM = field(default_factory=lambda: FallbackLLM("", "", configured=False))
    memory: MemoryConfig = field(default_factory=lambda: MemoryConfig(True, "~/.langrobo/qdrant"))
    health: HealthConfig = field(default_factory=lambda: HealthConfig(True, "0.0.0.0", 8090))
    log_json: bool = True


def load_settings() -> Settings:
    """Read + validate everything langrobo needs from the environment.

    Raises ConfigError on malformed values. Missing optional values yield a
    Settings object with those features disabled.
    """
    # ── Cloud LLM fallback ────────────────────────────────────────────────
    fb_provider = os.getenv("LANGROBO_FALLBACK_PROVIDER", "").strip().lower()
    fb_model = os.getenv("LANGROBO_FALLBACK_MODEL", "").strip()
    fb_key_env = os.getenv("LANGROBO_FALLBACK_API_KEY_ENV", "").strip()
    fb_base_url = os.getenv("LANGROBO_FALLBACK_BASE_URL", "").strip()
    if fb_provider and fb_provider not in _VALID_PROVIDERS:
        raise ConfigError(
            f"LANGROBO_FALLBACK_PROVIDER must be one of {_VALID_PROVIDERS}, got {fb_provider!r}")
    fb_key = os.environ.get(fb_key_env, "") if fb_key_env else ""
    fb_ready = bool(fb_provider and fb_model and (fb_key or fb_provider in ("llamacpp", "ollama")))
    if fb_provider and not fb_ready:
        logger.warning(
            "LLM fallback %s/%s is not usable yet — missing API key in $%s. "
            "The robot degrades to a spoken offline message if the primary LLM dies.",
            fb_provider, fb_model or "?", fb_key_env or "LANGROBO_FALLBACK_API_KEY_ENV")
    fallback = FallbackLLM(
        provider=fb_provider, model=fb_model, api_key=fb_key,
        base_url=fb_base_url, configured=fb_ready)

    # ── Episodic memory ───────────────────────────────────────────────────
    memory = MemoryConfig(
        enabled=_bool_env("LANGROBO_MEMORY", True),
        path=os.getenv("LANGROBO_MEMORY_PATH", "~/.langrobo/qdrant").strip(),
        url=os.getenv("QDRANT_URL", "").strip(),
        api_key=os.getenv("QDRANT_API_KEY", "").strip(),
    )

    # ── Health API ────────────────────────────────────────────────────────
    token = os.getenv("LANGROBO_API_TOKEN", "").strip()
    default_host = "0.0.0.0" if token else "127.0.0.1"
    health = HealthConfig(
        enabled=_bool_env("LANGROBO_HEALTH", True),
        host=os.getenv("LANGROBO_HEALTH_HOST", default_host).strip(),
        port=_int_env("LANGROBO_HEALTH_PORT", 8090, 1, 65535),
        token=token,
    )
    if health.enabled and not token and health.host not in ("127.0.0.1", "localhost"):
        # LAN-exposed but unauthenticated: refuse the combination rather than
        # silently serving robot state to the whole network.
        raise ConfigError(
            "LANGROBO_HEALTH_HOST is LAN-exposed but LANGROBO_API_TOKEN is empty. "
            "Set a token, or bind to 127.0.0.1.")

    settings = Settings(
        fallback=fallback,
        memory=memory,
        health=health,
        log_json=_bool_env("LANGROBO_LOG_JSON", True),
    )
    logger.info(
        "Settings: fallback=%s memory=%s(%s) health=%s:%s(token=%s)",
        fallback.provider or "none",
        "on" if memory.enabled else "off",
        memory.url or memory.path,
        health.host, health.port, "set" if token else "NONE — localhost only",
    )
    return settings


def sanitize_tracing_env() -> None:
    """Disable LangSmith tracing unless explicitly opted in.

    A stale LANGSMITH/LANGCHAIN key in .env spams a 403 on every LLM call.
    Tracing now requires the explicit opt-in LANGROBO_TRACING=true AND a key;
    anything else gets the tracing vars scrubbed from this process.
    """
    want = os.getenv("LANGROBO_TRACING", "").strip().lower() in ("1", "true", "yes", "on")
    has_key = bool(os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY"))
    if want and has_key:
        return
    scrubbed = [v for v in (
        "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING",
    ) if os.environ.pop(v, None) is not None]
    if scrubbed:
        reason = "no API key present" if want else "LANGROBO_TRACING not enabled"
        logger.info("LangSmith tracing disabled (%s) — scrubbed %s", reason, scrubbed)
