"""Services layer — config, llm, memory, telegram, health, logging, metrics.

Everything here is pure Python (no rclpy). The entry points (agent_node /
graph_studio) wire services together at startup:

    from langrobo_core.services import config, llm, memory, telegram, health
    from langrobo_core.services.logging import setup_logging, new_trace

    settings = config.load_settings()
    config.sanitize_tracing_env()
    llm.configure(...)                      # from ROS params / STUDIO_* env
    llm.configure_fallback(settings.fallback)
    memory.init(settings.memory)
    telegram.init(settings.telegram)
    health.start_health_api(settings.health, extra_status=...)
"""
