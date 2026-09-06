"""Services layer — config, llm, telegram, permissions, health, logging, metrics.

Everything here is pure Python (no rclpy). The entry points (agent_node /
graph_studio) wire services together at startup:

    from langrobo_core.services import config, llm, telegram, health
    from langrobo_core.services.logging import setup_logging, new_trace

    settings = config.load_settings()
    config.sanitize_tracing_env()
    llm.configure(...)                      # from ROS params
    llm.configure_fallback(settings.fallback)
    telegram.init(settings.telegram)
    health.start_health_api(settings.health, extra_status=...)

A service is state that outlives a turn (a Telegram poller, the LLM health
window, the health API). Anything that is only a function of its arguments
belongs in utils/ or in the tool that uses it.
"""
