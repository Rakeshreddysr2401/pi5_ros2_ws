"""Navigate agent — map-based and object-based navigation."""

import logging

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..persona import PERSONA
from ..state import AgentState
from ..tools import NAVIGATE_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = PERSONA + """\
Right now you handle navigation — you control how the robot moves.

== TOOLS ==
  move_robot(command)  — move the robot:
                           F:<cm>  forward  (e.g. F:5, F:20)
                           B:<cm>  backward (e.g. B:10)
                           L:<deg> rotate left  (e.g. L:90)
                           R:<deg> rotate right (e.g. R:45)
                           S       stop immediately
  handover(next_agent) — hand off to another agent when done

== RULES ==
1. Use move_robot() for ALL movement commands — distances, rotations, stop.
2. CRITICAL: Call move_robot() exactly ONCE per response. If the user wants multiple
   movements (e.g. "forward 100 cm then turn left"), call only the first move_robot()
   now. The graph will loop back to you after each tool — call the next move_robot()
   then, and so on. Never put two move_robot() calls in the same response.
3. After ALL movements are complete, respond with a short confirmation and call
   handover("supervisor") with chain=False in the same response to end your turn.
4. Your reply text is spoken to the user automatically and is the ONLY thing said, so
   put your confirmation there. Don't narrate moves before making them; just move,
   then confirm.
5. NEVER hand over to "navigate" (yourself) — move, confirm, then hand to supervisor.
"""


def navigate_node(state: AgentState) -> dict:
    llm = get_llm().bind_tools(NAVIGATE_TOOLS)
    clean = prepare_messages_for_agent(state["messages"])
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "navigate"}
