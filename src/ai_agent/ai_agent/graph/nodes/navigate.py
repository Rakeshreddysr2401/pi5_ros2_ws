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
  speak(text)          — tell the user what you're doing
  move_robot(command)  — move the robot:
                           F:<cm>  forward  (e.g. F:5, F:20)
                           B:<cm>  backward (e.g. B:10)
                           L:<deg> rotate left  (e.g. L:90)
                           R:<deg> rotate right (e.g. R:45)
                           S       stop immediately
  handover(next_agent) — hand off to another agent when done

== RULES ==
1. speak() briefly before moving so the user knows what's happening.
2. Use move_robot() for ALL movement commands — distances, rotations, stop.
3. After completing the movement, respond to the user with a short message confirming completion (e.g., "I've moved forward 10 cm.") and call handover("supervisor") with chain=False in the same response to end your turn.
"""


def navigate_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(NAVIGATE_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "navigate"}
