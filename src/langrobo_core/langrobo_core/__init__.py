"""langrobo_core — the pure-Python brain of the LangRobo home robot.

Zero ROS2 imports anywhere in this package (physically enforced: rclpy is not
a dependency). The ROS2 side lives in the langrobo_ros package, which injects
its bridge via langrobo_core.tools._bridge.init() at startup.

Layout:
    graph/     StateGraph topology, state, routing, handover resolution
    agents/    one module per agent (chat, local_agent, navigate, ...)
    tools/     @tool functions + per-agent tool sets
    services/  config, llm (+fallback), memory (Qdrant), health API, logging
    utils/     history trimming, message projection, speech streaming, timing
    bridges/   StubBridge for running without ROS2 (LangGraph Studio, tests)
"""

__version__ = "1.0.0"
