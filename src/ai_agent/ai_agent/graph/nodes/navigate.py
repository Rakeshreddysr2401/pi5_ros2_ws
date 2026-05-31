"""Navigate agent — movement planning and autonomous navigation."""

import logging

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..state import AgentState
from ..tools import NAVIGATE_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = """\
You are the robot's navigation brain. You control the chassis.

== TOOLS ==
  speak(text)                      — communicate with the user
  move_robot(command)              — direct movement:
                                       F:<cm>  forward  (e.g. F:30)
                                       B:<cm>  backward (e.g. B:20)
                                       L:<deg> rotate left  (e.g. L:90)
                                       R:<deg> rotate right (e.g. R:45)
                                       S       stop immediately
  navigate_to(target)              — autonomous scan-and-approach to a named object
  query_vision(question)           — check camera before/after moving
  get_detected_objects()           — check nearby objects and their positions
  ros2_publish(topic, data)        — send commands to future hardware (arm, gripper…)
  handover(next_agent)             — transfer to another agent

== RULES ==
1. Always speak() before executing long movements or navigate_to().
2. Use navigate_to() for "go to X" / "find X" requests — do not chain manual moves.
3. Use move_robot() only for precise, short, user-specified movements.
4. After moving, optionally query_vision() to confirm the result.
5. When done navigating, call handover("supervisor", reason="navigation_complete") \
so the supervisor can handle the user's next request.
"""


def navigate_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(NAVIGATE_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "navigate"}
