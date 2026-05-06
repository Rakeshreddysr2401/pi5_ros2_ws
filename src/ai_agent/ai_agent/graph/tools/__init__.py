"""Tool set exports — each agent node binds only the tools it needs.

Pattern mirrors owp_agent's tools/__init__.py:
  supervisor_tools, claim_validate_tools, rti_tools
"""

from .speech import speak
from .vision import query_vision, get_detected_objects
from .movement import move_robot, navigate_to
from .system import get_robot_status, ros2_publish

# Per-node tool sets (bound in each node via llm.bind_tools)
chat_tools      = []                   # pure conversation — no hardware access
vision_tools    = [speak, query_vision, get_detected_objects]
navigator_tools = [speak, move_robot, navigate_to, query_vision, get_detected_objects, ros2_publish]
status_tools    = [speak, get_robot_status, ros2_publish]

# Full set for ToolNode (must include every tool any node can call)
all_tools = [speak, query_vision, get_detected_objects, move_robot, navigate_to,
             get_robot_status, ros2_publish]
