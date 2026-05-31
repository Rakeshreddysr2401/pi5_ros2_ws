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


SWIGGY_FOOD_TOOLS: list = _load_sync()
