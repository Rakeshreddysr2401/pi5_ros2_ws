"""Agent nodes, built from the registry.

`NODES[name]` is the graph node; `BUILD_LLM_CALLS[name]` is the same agent's
prompt assembly, exported for agent_node's KV-cache warmer (it must prefill
exactly what the next real turn will send).
"""

from ..registry import SPECS
from .factory import build_agent
from .supervisor import supervisor_node, build_llm_call as _supervisor_build

NODES = {}
BUILD_LLM_CALLS = {}

for _name, _spec in SPECS.items():
    if _name == "supervisor":
        continue
    NODES[_name], BUILD_LLM_CALLS[_name] = build_agent(_spec)

# The supervisor is not a responder: it is forced to emit exactly one handover
# and never free text, so it keeps its own module.
NODES["supervisor"] = supervisor_node
BUILD_LLM_CALLS["supervisor"] = _supervisor_build

__all__ = ["NODES", "BUILD_LLM_CALLS"]
