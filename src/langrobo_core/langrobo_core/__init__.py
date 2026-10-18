"""langrobo_core — the pure-Python brain of the LangRobo home robot.

Zero ROS2 imports anywhere in this package (physically enforced: rclpy is not
a dependency). The ROS2 side lives in the langrobo_ros package, which injects
its bridge via langrobo_core.tools._bridge.init() at startup.

Layout:
    graph/     StateGraph topology, state, entry, handover resolution, backstops
    routing/   the on-device entry classifier (LANGROBO_INTENT_ROUTING)
    agents/    the factory: every agent is built from its registry.py AgentSpec
    tools/     @tool functions + per-agent tool sets
    services/  config, llm (+fallback), telegram, memory (SQLite facts), health API, logging
    utils/     history trimming, message projection, speech streaming, timing
    bridges/   StubBridge for running without ROS2 (LangGraph Studio, tests)
"""

__version__ = "1.0.0"
