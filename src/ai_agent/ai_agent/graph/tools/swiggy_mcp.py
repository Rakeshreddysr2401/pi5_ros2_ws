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


def _root_cause(exc: BaseException) -> BaseException:
    """Drill through ExceptionGroup/TaskGroup wrappers to the real error.

    The MCP client raises its failures inside an anyio TaskGroup, so the top-level
    message is the useless "unhandled errors in a TaskGroup" — unwrap to the leaf
    (e.g. the httpx 401) so the log actually says what went wrong."""
    seen = set()
    while True:
        if id(exc) in seen:
            return exc
        seen.add(id(exc))
        subs = getattr(exc, "exceptions", None)
        if subs:
            exc = subs[0]
        elif exc.__cause__ is not None:
            exc = exc.__cause__
        else:
            return exc


def _load_sync() -> list:
    try:
        try:
            asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _fetch_food_tools()).result()
        except RuntimeError:
            return asyncio.run(_fetch_food_tools())
    except Exception as e:
        cause = _root_cause(e)
        hint = ""
        if "401" in str(cause) or "Unauthorized" in str(cause):
            hint = " (check SWIGGY_ACCESS_TOKEN in .env)"
        logger.warning("Swiggy Food MCP unavailable — tools disabled: %s%s", cause, hint)
        return []


SWIGGY_FOOD_TOOLS: list = _load_sync()
