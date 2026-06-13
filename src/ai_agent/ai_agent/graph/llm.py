"""LLM factory — configured once at startup by agent_node.py.

Pattern mirrors owp_agent's llm_config.py:  a single get_llm() factory
function draws from a module-level config dict so every node gets the
same settings without passing the LLM object around explicitly.
"""

_config: dict = {}


def configure(
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    max_tokens: int,
) -> None:
    """Called once by agent_node before the graph is built."""
    global _config
    _config = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "max_tokens": max_tokens,
    }


def get_llm():
    """Return a fresh LLM instance using the configured provider."""
    provider   = _config.get("provider", "llamacpp")
    model      = _config.get("model", "default")
    base_url   = _config.get("base_url", "")
    api_key    = _config.get("api_key", "none")
    max_tokens = _config.get("max_tokens", 300)

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
