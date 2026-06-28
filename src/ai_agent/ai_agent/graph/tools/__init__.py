"""Tool set exports — each agent node binds only the tools it needs."""

from .speech import speak
from .look import look
from .movement import move_robot, navigate_to_pose, navigate_to_visible_object
from .system import get_robot_status, ros2_publish, set_active_order
from .handover import handover
from .swiggy_mcp import get_food_tools

# Per-agent tool sets
# Visual Q&A is handled by look() (camera frame → Gemma multimodal). The old
# query_vision() (Jetson Moondream) was retired — no local VLM fits the 8GB Jetson.
CHAT_TOOLS         = [speak, get_robot_status, handover]
LOCAL_AGENT_TOOLS  = [speak, look, handover]
NAVIGATE_TOOLS     = [speak, move_robot, navigate_to_pose, navigate_to_visible_object, handover]
STATUS_TOOLS       = [speak, get_robot_status, ros2_publish, handover]
SUPERVISOR_TOOLS   = [handover]

# Swiggy/tracker pull in the Swiggy MCP tools, which load over the network. Exposed
# as memoized FUNCTIONS (not constants) so that load happens at graph build time,
# once, and never at import. Call get_swiggy_tools() / get_tracker_tools().
_SWIGGY_BASE  = [speak, set_active_order, handover]
_TRACKER_BASE = [speak, set_active_order, navigate_to_pose, handover]
_swiggy_cache:  list | None = None
_tracker_cache: list | None = None


def get_swiggy_tools() -> list:
    global _swiggy_cache
    if _swiggy_cache is None:
        _swiggy_cache = _SWIGGY_BASE + get_food_tools()
    return _swiggy_cache


def get_tracker_tools() -> list:
    global _tracker_cache
    if _tracker_cache is None:
        _tracker_cache = _TRACKER_BASE + get_food_tools()
    return _tracker_cache
