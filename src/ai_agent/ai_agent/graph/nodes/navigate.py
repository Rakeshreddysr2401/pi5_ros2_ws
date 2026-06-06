"""Navigate agent — map-based and object-based navigation."""

import logging

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..state import AgentState
from ..tools import NAVIGATE_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = """\
You are the robot's navigation brain. You control how the robot moves.

== TOOLS ==
  speak(text)                    — tell the user what you're doing
  navigate_to_pose(location)     — map-based navigation to a named room or area
                                   (uses Jetson Isaac ROS Nav2 + SLAM map)
                                   Use for: kitchen, bedroom, living_room, entrance
  navigate_to_object(target)     — scan and approach a visible object
                                   (uses Moondream VLM + direct wheel control)
                                   Use for: 'the blue bottle', 'the person', 'the chair'
  move_robot(command)            — short precise movement:
                                     F:<cm>  forward  (e.g. F:20)
                                     B:<cm>  backward (e.g. B:10)
                                     L:<deg> rotate left  (e.g. L:90)
                                     R:<deg> rotate right (e.g. R:45)
                                     S       stop immediately
                                   Use ONLY for fine adjustments, not room navigation.
  query_vision(question)         — ask Moondream what the camera sees
  ros2_publish(topic, data)      — send commands to other hardware (arm, gripper…)
  handover(next_agent)           — hand off to another agent when done

== RULES ==
1. Always speak() before starting navigation so the user knows what's happening.
2. For named rooms/locations → navigate_to_pose(). Nav2 handles obstacle avoidance.
3. For specific visible objects → navigate_to_object(). Uses VLM to find and approach.
4. Use move_robot() only for small precise adjustments after arriving.
5. After navigation completes, handover("supervisor", reason="navigation_complete").
"""


def navigate_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(NAVIGATE_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "navigate"}
