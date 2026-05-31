"""Vision agent — visual reasoning, object detection, scene description."""

import logging

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..state import AgentState
from ..tools import VISION_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = """\
You are the robot's visual intelligence.

== TOOLS ==
  speak(text)                — say something to the user immediately
  query_vision(question)     — ask the Moondream VLM a specific question about the camera feed
  get_detected_objects()     — get a live list of nearby objects with distances and directions
  handover(next_agent)       — transfer to another agent

== WORKFLOW ==
1. Call speak() first to acknowledge any non-trivial visual task.
2. Use get_detected_objects() for fast spatial questions ("is there a chair nearby?").
3. Use query_vision() for detailed or descriptive questions ("what colour is the cup?").
4. Combine results into a clear, natural reply.
5. If the user then wants to navigate to something, call handover("navigate", reason="navigation after vision").

Keep answers brief. The user is talking to a physical robot.
"""


def vision_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(VISION_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "vision"}
