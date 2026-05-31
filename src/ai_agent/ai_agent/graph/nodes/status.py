"""Status agent — robot operational state, battery, hardware queries."""

import logging

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..state import AgentState
from ..tools import STATUS_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = """\
You are the robot's system monitor.

== TOOLS ==
  speak(text)                — say something to the user
  get_robot_status()         — query battery level, current task, hardware state
  ros2_publish(topic, data)  — publish to any ROS2 topic for advanced control
  handover(next_agent)       — transfer to another agent

Answer questions about the robot's operational state accurately and concisely.
If a service is unavailable, say so honestly rather than guessing.
After answering, call handover("supervisor", reason="status_answered") so the \
supervisor can handle the user's next request.
"""


def status_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(STATUS_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "status"}
