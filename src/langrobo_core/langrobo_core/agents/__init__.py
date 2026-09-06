"""Agent nodes, built from the registry.

`NODES[name]` is the graph node; `BUILD_LLM_CALLS[name]` is the same agent's
prompt assembly, exported for agent_node's KV-cache warmer (it must prefill
exactly what the next real turn will send).

Every agent is built by the same factory. `supervisor.py` used to be the one
hand-written exception — a node with forced tool_choice that could only emit a
handover. It went with the supervisor itself; see agent_ids.py.
"""

from ..registry import SPECS
from .factory import build_agent

NODES = {}
BUILD_LLM_CALLS = {}

for _name, _spec in SPECS.items():
    NODES[_name], BUILD_LLM_CALLS[_name] = build_agent(_spec)

__all__ = ["NODES", "BUILD_LLM_CALLS"]
