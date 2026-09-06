"""LangGraph Studio entry point.

Exports `graph` — referenced by langgraph.json as ./graph_studio.py:graph.

When ROS2 is sourced and available, connects to a real ROS2Bridge so Studio
can drive the physical robot.  Otherwise falls back to a StubBridge that
logs tool calls without publishing to ROS2 topics.

Usage on Pi5:
    source /opt/ros/jazzy/setup.bash    # optional — enables real robot control
    langgraph dev                        # run from repo root

Usage on dev machine (no ROS2):
    langgraph dev                        # chat works; robot tools just log
"""

import atexit
import logging
import os
import threading

from dotenv import load_dotenv

load_dotenv()  # reads .env from cwd (repo root) before any langrobo imports

from langrobo_core.services import config as config_service
from langrobo_core.services import llm as llm_module
from langrobo_core.services import telegram as telegram_service
from langrobo_core.tools import _bridge as bridge_module

logger = logging.getLogger(__name__)

# ── Services (same wiring as agent_node, minus the health API — Studio is a
#    dev tool; the robot process owns the health port) ────────────────────────

config_service.sanitize_tracing_env()
_settings = config_service.load_settings()

# ── Known map locations (same defaults as agent_params.yaml) ─────────────────

_DEFAULT_LOCATIONS = {
    "kitchen":     (2.5,  1.0,   0.0),
    "living_room": (0.0,  3.0,  90.0),
    "bedroom":     (-2.0, 2.0, 180.0),
    "entrance":    (0.0,  0.0,   0.0),
}

# ── LLM config from environment ───────────────────────────────────────────────

_PROVIDER_KEY_MAP = {
    "openai":    "OPENAI_API_KEY",
    "llamacpp":  "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini":    "GOOGLE_API_KEY",
    "ollama":    "",
}

_provider   = os.getenv("STUDIO_PROVIDER", "openai")
_model      = os.getenv("STUDIO_MODEL",    "gpt-4o-mini")
_base_url   = os.getenv("STUDIO_BASE_URL", "")
_max_tokens = int(os.getenv("STUDIO_MAX_TOKENS", "3000"))

_key_env = _PROVIDER_KEY_MAP.get(_provider, "OPENAI_API_KEY")
_api_key = os.getenv(_key_env, "none") if _key_env else "none"

# One KV slot per agent, straight from the registry — the same map agent_node
# uses, so a graph stepped in Studio has the same cache behaviour as the robot.
from langrobo_core import registry
llm_module.configure(_provider, _model, _base_url, _api_key, _max_tokens,
                     {n: {"slot": s} for n, s in registry.SLOTS.items()})
llm_module.configure_fallback(_settings.fallback)
telegram_service.init(_settings.telegram)
logger.info("Studio LLM: provider=%s model=%s slots=%s",
            _provider, _model, registry.SLOTS)

# ── Bridge: real ROS2 or stub ─────────────────────────────────────────────────

_ros2_node = None


def _try_ros2_bridge():
    """Attempt to create a real ROS2Bridge with a spinning background thread.

    Hot-reload safe: if rclpy is already initialised (langgraph dev watches
    files and reimports), reuses the existing context and destroys the old node.
    """
    global _ros2_node
    try:
        import rclpy
        from langrobo_ros.ros2_bridge import ROS2Bridge

        # Destroy previous node on hot-reload
        if _ros2_node is not None:
            try:
                _ros2_node.destroy_node()
            except Exception:
                pass
            _ros2_node = None

        if not rclpy.ok():
            rclpy.init()
            atexit.register(_shutdown_rclpy)

        _ros2_node = rclpy.create_node("studio_bridge")
        bridge = ROS2Bridge(_ros2_node, known_locations=_DEFAULT_LOCATIONS)

        threading.Thread(
            target=rclpy.spin,
            args=(_ros2_node,),
            daemon=True,
            name="studio_ros2_spin",
        ).start()

        logger.info("ROS2 bridge active — tools will control the real robot")
        return bridge

    except Exception as exc:
        logger.warning("ROS2 unavailable (%s) — using stub bridge", exc)
        return None


def _shutdown_rclpy():
    try:
        import rclpy
        if rclpy.ok():
            rclpy.shutdown()
    except Exception:
        pass


def _make_stub_bridge():
    from langrobo_core.bridges import StubBridge
    bridge = StubBridge(known_locations=_DEFAULT_LOCATIONS)
    logger.info("Stub bridge active — robot tools will log instead of publishing")
    return bridge


_active_bridge = _try_ros2_bridge() or _make_stub_bridge()
bridge_module.init(_active_bridge)

# ── Build and export graph ────────────────────────────────────────────────────

from langrobo_core.graph import build_graph

graph = build_graph()

__all__ = ["graph"]
