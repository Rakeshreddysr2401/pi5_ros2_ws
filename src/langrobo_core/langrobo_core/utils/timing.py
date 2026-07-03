"""Pipeline stage timing — pure zone (no rclpy).

Every latency-relevant stage in the voice pipeline emits a small JSON event.
agent_node injects a sink that publishes events on /diag/timing so the replay
harness (scripts/latency_replay.py) can print a per-turn waterfall; without a
sink (LangGraph Studio) events fall back to the module logger.

Event shape: {"stage": str, "t": <wall-clock epoch seconds>, ...extra fields}
Cross-machine correlation relies on NTP-synced clocks (Pi5/Jetson/Mac).
"""

import logging
import time
from typing import Callable, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

_sink: Optional[Callable[[dict], None]] = None


def set_sink(fn: Callable[[dict], None]) -> None:
    """Install the event sink. agent_node passes bridge.publish_timing."""
    global _sink
    _sink = fn


def emit(stage: str, **fields) -> None:
    """Emit one stage event. Never raises — timing must not break the pipeline."""
    event = {"stage": stage, "t": time.time(), **fields}
    try:
        if _sink is not None:
            _sink(event)
        else:
            logger.info("timing: %s", event)
    except Exception:
        logger.debug("timing sink failed for %s", stage, exc_info=True)


class TimingCallbackHandler(BaseCallbackHandler):
    """Emits llm_start / llm_end for every chat-model call in the graph.

    Pass once in the graph config: graph.stream(..., config={"callbacks": [h]}).
    langchain propagates it into each node's llm.invoke(); the LangGraph node
    name arrives in metadata["langgraph_node"], so events say which agent paid.
    """

    raise_error = False

    def __init__(self):
        self._inflight: dict[UUID, tuple[str, float]] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        agent = (metadata or {}).get("langgraph_node", "unknown")
        self._inflight[run_id] = (agent, time.time())
        emit("llm_start", agent=agent)

    def on_llm_end(self, response, *, run_id, **kwargs):
        agent, started = self._inflight.pop(run_id, ("unknown", None))
        fields = {"agent": agent}
        if started is not None:
            fields["dur_s"] = round(time.time() - started, 3)
        usage = self._extract_usage(response)
        if usage:
            fields.update(usage)
        emit("llm_end", **fields)

    def on_llm_error(self, error, *, run_id, **kwargs):
        agent, started = self._inflight.pop(run_id, ("unknown", None))
        fields = {"agent": agent, "error": str(error)[:120]}
        if started is not None:
            fields["dur_s"] = round(time.time() - started, 3)
        emit("llm_error", **fields)

    @staticmethod
    def _extract_usage(response) -> dict:
        try:
            gen = response.generations[0][0]
            usage = getattr(gen.message, "usage_metadata", None) or {}
            return {
                k: usage[src]
                for k, src in (("in_tok", "input_tokens"), ("out_tok", "output_tokens"))
                if src in usage
            }
        except Exception:
            return {}
