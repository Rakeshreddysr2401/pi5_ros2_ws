"""LLM factory — configured once at startup by agent_node.py.

Pattern mirrors owp_agent's llm_config.py:  a single get_llm() factory
function draws from a module-level config dict so every node gets the
same settings without passing the LLM object around explicitly.

Per-agent overrides:  get_llm(agent_name) merges the global config with an
optional per-agent override dict.  This lets one agent (e.g. "local_agent")
pin a dedicated llama.cpp KV-cache slot via `slot`, or run on a different
provider/model entirely — without disturbing the others.  See agent_params.yaml.
"""

_config: dict = {}
_agent_overrides: dict = {}


def configure(
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    max_tokens: int,
    agent_overrides: dict | None = None,
) -> None:
    """Called once by agent_node before the graph is built.

    agent_overrides: {agent_name: {provider?, model?, base_url?, api_key?,
                      max_tokens?, slot?}} — any subset of fields overrides
                      the global config for that agent only.
    """
    global _config, _agent_overrides
    _config = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "max_tokens": max_tokens,
    }
    _agent_overrides = agent_overrides or {}


def get_llm(agent: str | None = None):
    """Return a fresh LLM instance for `agent` (or the global default).

    Merges the global config with any per-agent override.  For llama.cpp /
    openai providers, a non-negative `slot` is forwarded as `id_slot` so the
    server keeps that agent's KV cache in its own slot (no cross-agent eviction).
    """
    cfg = dict(_config)
    if agent and agent in _agent_overrides:
        cfg.update({k: v for k, v in _agent_overrides[agent].items() if v is not None})

    provider   = cfg.get("provider", "llamacpp")
    model      = cfg.get("model", "default")
    base_url   = cfg.get("base_url", "")
    api_key    = cfg.get("api_key", "none")
    max_tokens = cfg.get("max_tokens", 300)
    slot       = cfg.get("slot")

    if provider in ("openai", "llamacpp"):
        from langchain_openai import ChatOpenAI
        kwargs: dict = {
            "model": model,
            "api_key": api_key,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if base_url and not (provider == "openai" and "singireddys-mac-mini" in base_url):
            kwargs["base_url"] = base_url
        # Pin a llama.cpp KV-cache slot for this agent (server needs --parallel N).
        # Forwarded verbatim into the request body via the OpenAI client's extra_body.
        if slot is not None and slot >= 0:
            kwargs["extra_body"] = {"id_slot": slot}
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, api_key=api_key, max_tokens=max_tokens)

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, base_url=base_url or "http://localhost:11434")

    raise ValueError(
        f"Unknown provider: {provider!r}. "
        "Choose from: llamacpp | openai | anthropic | gemini | ollama"
    )
