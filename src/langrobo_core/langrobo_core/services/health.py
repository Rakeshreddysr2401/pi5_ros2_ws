"""In-process health/status/metrics API — pure zone.

Runs a small FastAPI app on a daemon thread inside the brain process — it must
be in-process, because the turn state it reports lives there.

Endpoints:
  GET /health   — liveness: 200 {"ok": true} whenever the process is up.
                  Unauthenticated by design (systemd/uptime probes need it).
  GET /status   — full state: LLM health, memory, last turn, counters.
  GET /metrics  — Prometheus text format (Grafana can bolt on later).

Auth: /status and /metrics require `Authorization: Bearer $LANGROBO_API_TOKEN`
when a token is configured. With no token, config forces binding to 127.0.0.1
(services.config refuses the LAN-exposed + tokenless combination).

fastapi/uvicorn missing → the API logs one warning and stays off; the robot
runs fine without it.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from . import llm as llm_service
from . import metrics
from .config import HealthConfig

logger = logging.getLogger(__name__)


def start_health_api(cfg: HealthConfig,
                     extra_status: Callable[[], dict] | None = None) -> bool:
    """Start the API on a daemon thread. Returns True if it started.

    extra_status: entry-point hook that contributes runtime state (bridge
    frame age, sticky agent, queue depth) into GET /status.
    """
    if not cfg.enabled:
        logger.info("Health API disabled (LANGROBO_HEALTH=false)")
        return False
    try:
        import uvicorn
        from fastapi import Depends, FastAPI, Header, HTTPException
        from fastapi.responses import PlainTextResponse
    except ImportError as e:
        logger.warning("Health API unavailable — %s (pip install fastapi uvicorn)", e)
        return False

    # NB: plain `str` annotation on purpose — this module uses PEP 563
    # (stringized) annotations, and FastAPI can only resolve names that exist
    # at module scope; fastapi types are imported lazily inside this function.
    def require_token(authorization: str = Header(default="")) -> None:
        if not cfg.token:
            return  # tokenless mode is localhost-only, enforced by config
        if authorization != f"Bearer {cfg.token}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    app = FastAPI(title="langrobo", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/status", dependencies=[Depends(require_token)])
    def status() -> dict:
        out = {
            "llm": llm_service.status(),
            "metrics": metrics.snapshot(),
        }
        if extra_status:
            try:
                out["runtime"] = extra_status()
            except Exception as e:
                out["runtime"] = {"error": str(e)}
        return out

    @app.get("/metrics", dependencies=[Depends(require_token)])
    def prom() -> PlainTextResponse:
        return PlainTextResponse(metrics.render_prometheus(),
                                 media_type="text/plain; version=0.0.4")

    server = uvicorn.Server(uvicorn.Config(
        app, host=cfg.host, port=cfg.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="health_api").start()
    logger.info("Health API on http://%s:%d (/health /status /metrics, token=%s)",
                cfg.host, cfg.port, "required" if cfg.token else "off — localhost only")
    return True
