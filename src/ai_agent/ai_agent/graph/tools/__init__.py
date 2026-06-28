"""Tool set exports — each agent node binds only the tools it needs."""

from .speech import speak
from .look import look
from .movement import move_robot, navigate_to_pose, navigate_to_visible_object
from .system import get_robot_status, ros2_publish, set_active_order
from .handover import handover
from .swiggy_mcp import SWIGGY_FOOD_TOOLS

# Per-agent tool sets
# Visual Q&A is handled by look() (camera frame → Gemma multimodal). The old
# query_vision() (Jetson Moondream) was retired — no local VLM fits the 8GB Jetson.
CHAT_TOOLS         = [speak, get_robot_status, handover]
LOCAL_AGENT_TOOLS  = [speak, look, handover]
NAVIGATE_TOOLS     = [speak, move_robot, navigate_to_pose, navigate_to_visible_object, handover]
STATUS_TOOLS       = [speak, get_robot_status, ros2_publish, handover]
SUPERVISOR_TOOLS   = [handover]
SWIGGY_TOOLS       = [speak, set_active_order, handover] + SWIGGY_FOOD_TOOLS
TRACKER_TOOLS      = [speak, set_active_order, navigate_to_pose, handover] + SWIGGY_FOOD_TOOLS
