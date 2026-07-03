"""Tiny in-process metrics registry — pure zone, no prometheus_client dependency.

Thread-safe counters and gauges, rendered to Prometheus text format by the
health API's /metrics endpoint. Deliberately minimal: a home robot needs a
dozen series, not a metrics framework.

Usage:
    from langrobo_core.services import metrics
    metrics.inc("turns_total")
    metrics.set_gauge("last_turn_duration_seconds", 3.2)
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_counters: dict[str, float] = {}
_gauges: dict[str, float] = {}
_started_at = time.time()


def inc(name: str, amount: float = 1.0) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0.0) + amount


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = value


def snapshot() -> dict:
    """Point-in-time copy for /status."""
    with _lock:
        return {
            "uptime_seconds": round(time.time() - _started_at, 1),
            "counters": dict(_counters),
            "gauges": dict(_gauges),
        }


def render_prometheus() -> str:
    """Render all series in Prometheus text exposition format."""
    lines = [
        "# TYPE langrobo_uptime_seconds gauge",
        f"langrobo_uptime_seconds {time.time() - _started_at:.1f}",
    ]
    with _lock:
        for name, val in sorted(_counters.items()):
            lines.append(f"# TYPE langrobo_{name} counter")
            lines.append(f"langrobo_{name} {val}")
        for name, val in sorted(_gauges.items()):
            lines.append(f"# TYPE langrobo_{name} gauge")
            lines.append(f"langrobo_{name} {val}")
    return "\n".join(lines) + "\n"
