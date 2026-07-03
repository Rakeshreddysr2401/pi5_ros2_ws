"""Structured logging with per-turn trace IDs — pure zone (no rclpy).

setup_logging() installs a JSON formatter on the root logger so every line —
from any module, including langchain's — carries a timestamp, level, logger
name, and the current turn's trace_id. journald/systemd captures stdout, so
`journalctl -u langrobo-brain -o cat | jq` gives filterable structured logs.

Trace IDs use a ContextVar: agent_node calls new_trace() at the start of each
turn (worker thread), and every log record emitted while that turn runs —
graph nodes, tools, services — is stamped with the same id. Timing events
(utils.timing) carry it too, so /diag/timing waterfalls join up with logs.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("langrobo_trace", default="")


def new_trace() -> str:
    """Start a new trace (one per turn). Returns the id for cross-referencing."""
    tid = uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    return tid


def current_trace() -> str:
    return _trace_id.get()


class _TraceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Extra fields via logger.info(..., extra={...})."""

    _RESERVED = frozenset(vars(logging.makeLogRecord({})).keys()) | {"trace_id", "taskName"}

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": round(record.created, 3),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
                    + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        trace = getattr(record, "trace_id", "")
        if trace:
            out["trace_id"] = trace
        for key, val in vars(record).items():
            if key not in self._RESERVED and not key.startswith("_"):
                out[key] = val
        if record.exc_info and record.exc_info[0] is not None:
            out["exc"] = self.formatException(record.exc_info)
        try:
            return json.dumps(out, default=str)
        except (TypeError, ValueError):
            return json.dumps({"level": record.levelname, "msg": record.getMessage()})


def setup_logging(level: int = logging.INFO, json_format: bool = True) -> None:
    """Install the structured handler on the root logger (idempotent).

    Replaces existing stream handlers so double-logging can't happen when both
    rclpy and langgraph configure logging.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(trace_id)s %(message)s"))
    handler.addFilter(_TraceFilter())
    root.addHandler(handler)
    # These libraries log noisily at INFO; keep them at WARNING.
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
