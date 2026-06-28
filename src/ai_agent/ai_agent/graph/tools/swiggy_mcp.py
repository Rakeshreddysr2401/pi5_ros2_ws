import asyncio
import concurrent.futures
import logging
import os

logger = logging.getLogger(__name__)

_FOOD_MCP_URL = os.getenv("SWIGGY_FOOD_MCP_URL", "https://mcp.swiggy.com/food")
_ACCESS_TOKEN = os.getenv("SWIGGY_ACCESS_TOKEN", "")


async def _fetch_food_tools() -> list:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    headers = {}
    if _ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {_ACCESS_TOKEN}"

    client = MultiServerMCPClient(
        {
            "swiggy_food": {
                "transport": "streamable_http",
                "url": _FOOD_MCP_URL,
                "headers": headers,
            }
        }
    )
    return await client.get_tools()


def _load_sync() -> list:
    try:
        try:
            asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _fetch_food_tools()).result()
        except RuntimeError:
            return asyncio.run(_fetch_food_tools())
    except Exception as e:
        logger.warning("Swiggy Food MCP unavailable — tools disabled: %s", e)
        return []


# Cache: None = not loaded yet; list = loaded result (possibly []).
_food_tools_cache: list | None = None


def get_food_tools() -> list:
    """Lazily fetch the Swiggy Food MCP tools once and cache the result.

    Network I/O happens on the FIRST call (made at graph build / startup), never at
    import time, so importing the tools/graph package can't hang on the network or
    during tests. Never raises — returns [] if the MCP is unavailable.

    Set SWIGGY_ENABLED=0 to skip the load entirely (no network at all).
    """
    global _food_tools_cache
    if _food_tools_cache is not None:
        return _food_tools_cache
    if os.getenv("SWIGGY_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        logger.info("Swiggy disabled via SWIGGY_ENABLED — skipping MCP load")
        _food_tools_cache = []
    else:
        _food_tools_cache = _load_sync()
    return _food_tools_cache
