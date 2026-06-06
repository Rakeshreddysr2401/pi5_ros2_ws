"""Tool set exports — each agent node binds only the tools it needs."""

from .speech import speak
from .vision import query_vision
from .movement import move_robot, navigate_to_pose, navigate_to_object
from .system import get_robot_status, ros2_publish

# Per-agent tool sets
CHAT_TOOLS      = [speak, query_vision, get_robot_status]
VISION_TOOLS    = [speak, query_vision]
NAVIGATE_TOOLS  = [speak, move_robot, navigate_to_pose, navigate_to_object, query_vision, ros2_publish]
STATUS_TOOLS    = [speak, get_robot_status, ros2_publish]

# Full set for ToolNode — must include every tool any agent can call
ALL_TOOLS = [
    speak,
    query_vision,
    move_robot,
    navigate_to_pose,
    navigate_to_object,
    get_robot_status,
    ros2_publish,
]
