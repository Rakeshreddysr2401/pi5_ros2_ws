"""Module-level bridge reference — the ROS2 adapter shared by all tools.

Pattern mirrors owp_agent's bearer_token_var (ContextVar):
a single module-level object that every tool imports.
agent_node.py calls init() once at startup before building the graph.
"""

_instance = None


def init(bridge) -> None:
    """Inject the ROS2Bridge instance.  Must be called before build_graph()."""
    global _instance
    _instance = bridge


def get():
    """Return the active bridge.  Raises if init() was never called."""
    if _instance is None:
        raise RuntimeError(
            "ROS2 bridge not initialised.  Call graph.tools._bridge.init(bridge) first."
        )
    return _instance
