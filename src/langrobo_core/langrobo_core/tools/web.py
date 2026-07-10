"""Web search tool (Tavily).

Loaded once at import time, mirroring services/mcp.py: if TAVILY_API_KEY is not set
(e.g. on the Pi5 without a key) the tool is simply disabled and WEB_TOOLS is empty,
so binding it is a no-op. The chat prompt advertises this as "tavily_search (if
available)" — that wording matches this optional wiring.

We call Tavily with include_answer=True and search_depth="basic": Tavily returns a
short synthesized answer plus compact snippets, instead of dumping whole scraped
pages. That keeps the answer accurate and the context small — important for the
low-token Gemma model that reads the result.
"""

import logging
import os

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_MAX_RESULTS = 3
_SNIPPET_CHARS = 300


def _build_web_tools() -> list:
    if not os.getenv("TAVILY_API_KEY"):
        logger.info("TAVILY_API_KEY not set — web search disabled")
        return []
    try:
        from langchain_community.utilities.tavily_search import TavilySearchAPIWrapper
    except Exception as e:
        logger.warning("Tavily import failed — web search disabled: %s", e)
        return []

    _wrapper = TavilySearchAPIWrapper()

    @tool
    def tavily_search(query: str) -> str:
        """Search the web for current, real-time information (weather, news, live
        facts, prices, times). Use this whenever the answer depends on up-to-date
        data you cannot know from memory. Returns a short synthesized answer plus a
        few source snippets — read them and reply in your own words."""
        try:
            raw = _wrapper.raw_results(
                query=query,
                max_results=_MAX_RESULTS,
                search_depth="basic",
                include_answer=True,
                include_raw_content=False,
            )
        except Exception as e:
            logger.warning("Tavily search failed: %s", e)
            return f"Web search failed: {e}"

        parts = []
        answer = (raw.get("answer") or "").strip()
        if answer:
            parts.append(f"Answer: {answer}")

        for r in raw.get("results", [])[:_MAX_RESULTS]:
            title = (r.get("title") or "").strip()
            content = (r.get("content") or "").strip()[:_SNIPPET_CHARS]
            if content:
                parts.append(f"- {title}: {content}")

        return "\n".join(parts) if parts else "No results found."

    logger.info("Tavily web search enabled")
    return [tavily_search]


WEB_TOOLS: list = _build_web_tools()
