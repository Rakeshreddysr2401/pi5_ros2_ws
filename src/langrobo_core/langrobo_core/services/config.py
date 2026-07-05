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

from . import permissions

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


def _float_env(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}")
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
    # Durable facts distilled from episodes by services/consolidation.py.
    facts_collection: str = "facts"
    # Household knowledge base — ingested documents (services/knowledge.py).
    knowledge_collection: str = "knowledge"
    embed_model: str = "BAAI/bge-small-en-v1.5"   # fastembed ONNX, 384-dim


@dataclass(frozen=True)
class TelegramMember:
    chat_id: int
    name: str
    role: str                  # one of permissions.ROLES


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram channel (services.telegram). Off unless both the bot token and
    a non-empty allowlist are present — a bot with nobody to talk to stays off."""
    token: str = ""            # from @BotFather, never logged
    members: tuple = ()        # TelegramMember entries — the ONLY people the bot serves
    configured: bool = False
    # Proactive pings (reminder/delivery [SYSTEM] turns) queue during this
    # window and flush after it; direct replies always go through. Minutes
    # since midnight (start, end), overnight wrap allowed. None = no window.
    quiet: tuple | None = None


def _parse_quiet_hours(raw: str) -> tuple | None:
    """LANGROBO_QUIET_HOURS = 'HH:MM-HH:MM' (e.g. 23:00-07:00)."""
    if not raw:
        return None
    try:
        start_s, end_s = raw.split("-")
        parts = []
        for s in (start_s, end_s):
            hh, mm = s.strip().split(":")
            hh, mm = int(hh), int(mm)
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError
            parts.append(hh * 60 + mm)
        if parts[0] == parts[1]:
            raise ValueError   # zero-length window is a typo, not "all day"
        return tuple(parts)
    except ValueError:
        raise ConfigError(
            f"LANGROBO_QUIET_HOURS must be 'HH:MM-HH:MM' (e.g. 23:00-07:00), got {raw!r}")


def _parse_telegram_allowlist(raw: str) -> tuple:
    """LANGROBO_TELEGRAM_ALLOWLIST = 'chat_id:Name:role, chat_id:Name:role, …'."""
    members: list[TelegramMember] = []
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) != 3 or not all(parts):
            raise ConfigError(
                f"LANGROBO_TELEGRAM_ALLOWLIST entry must be 'chat_id:Name:role', got {entry!r}")
        chat_id_raw, name, role = parts
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            raise ConfigError(
                f"LANGROBO_TELEGRAM_ALLOWLIST chat_id must be an integer, got {chat_id_raw!r}")
        role = role.lower()
        if role not in permissions.ROLES:
            raise ConfigError(
                f"LANGROBO_TELEGRAM_ALLOWLIST role must be one of {permissions.ROLES}, "
                f"got {role!r} for {name!r}")
        if any(m.chat_id == chat_id for m in members):
            raise ConfigError(f"LANGROBO_TELEGRAM_ALLOWLIST has duplicate chat_id {chat_id}")
        if any(m.name.casefold() == name.casefold() for m in members):
            # Names are how the agent addresses recipients — ambiguity would
            # let "send to Mom" pick the wrong person.
            raise ConfigError(f"LANGROBO_TELEGRAM_ALLOWLIST has duplicate name {name!r}")
        members.append(TelegramMember(chat_id=chat_id, name=name, role=role))
    return tuple(members)


@dataclass(frozen=True)
class WatchConfig:
    """Home watch mode (services/watch.py): while armed, a person detected by
    the Jetson target finder triggers a photo alert to owners' phones. Armed
    state persists across restarts (an armed house stays armed)."""
    enabled: bool = True
    state_path: str = "~/.langrobo/watch.json"
    cooldown_s: int = 60           # min seconds between alerts (one visitor ≠ 50 pings)
    min_confidence: float = 0.5    # YOLO person confidence below this is ignored


@dataclass(frozen=True)
class ConsolidationConfig:
    """Nightly memory consolidation (services/consolidation.py): distill new
    episodic turns into durable facts, locally. Runs only when the robot is
    idle and the LOCAL model is up — never on the cloud fallback."""
    enabled: bool = True
    hour: int = 3                  # local hour of day the job becomes eligible
    state_path: str = "~/.langrobo/consolidation.json"
    min_episodes: int = 5          # skip the run below this many new episodes
    max_episodes: int = 200        # cap one run's input (rest picked up next night)
    batch_size: int = 25           # episodes per LLM call


@dataclass(frozen=True)
class BriefingConfig:
    """Scheduled morning briefing (briefing agent via the [SYSTEM] producer).
    Off unless LANGROBO_BRIEFING_HOUR is set — a robot that starts talking at
    8am unasked must be opted into."""
    enabled: bool = False
    hour: int = 8
    state_path: str = "~/.langrobo/briefing.json"


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
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    consolidation: ConsolidationConfig = field(default_factory=ConsolidationConfig)
    briefing: BriefingConfig = field(default_factory=BriefingConfig)
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

    # ── Telegram channel ──────────────────────────────────────────────────
    tg_token = os.getenv("LANGROBO_TELEGRAM_TOKEN", "").strip()
    tg_members = _parse_telegram_allowlist(os.getenv("LANGROBO_TELEGRAM_ALLOWLIST", ""))
    if tg_token and not tg_members:
        logger.warning(
            "LANGROBO_TELEGRAM_TOKEN is set but LANGROBO_TELEGRAM_ALLOWLIST is empty — "
            "Telegram stays disabled (the bot must never talk to strangers).")
    telegram = TelegramConfig(
        token=tg_token, members=tg_members,
        configured=bool(tg_token and tg_members),
        quiet=_parse_quiet_hours(os.getenv("LANGROBO_QUIET_HOURS", "").strip()))

    # ── Home watch mode ───────────────────────────────────────────────────
    watch = WatchConfig(
        enabled=_bool_env("LANGROBO_WATCH", True),
        cooldown_s=_int_env("LANGROBO_WATCH_COOLDOWN_S", 60, 5, 3600),
        min_confidence=_float_env("LANGROBO_WATCH_MIN_CONF", 0.5, 0.0, 1.0),
    )

    # ── Memory consolidation ──────────────────────────────────────────────
    consolidation = ConsolidationConfig(
        enabled=_bool_env("LANGROBO_CONSOLIDATION", True),
        hour=_int_env("LANGROBO_CONSOLIDATION_HOUR", 3, 0, 23),
    )

    # ── Morning briefing (opt-in: enabled only when the hour is set) ──────
    briefing = BriefingConfig(
        enabled=bool(os.getenv("LANGROBO_BRIEFING_HOUR", "").strip()),
        hour=_int_env("LANGROBO_BRIEFING_HOUR", 8, 0, 23),
    )

    settings = Settings(
        fallback=fallback,
        memory=memory,
        health=health,
        telegram=telegram,
        watch=watch,
        consolidation=consolidation,
        briefing=briefing,
        log_json=_bool_env("LANGROBO_LOG_JSON", True),
    )
    logger.info(
        "Settings: fallback=%s memory=%s(%s) health=%s:%s(token=%s) telegram=%s",
        fallback.provider or "none",
        "on" if memory.enabled else "off",
        memory.url or memory.path,
        health.host, health.port, "set" if token else "NONE — localhost only",
        f"{len(tg_members)} members" if telegram.configured else "off",
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
