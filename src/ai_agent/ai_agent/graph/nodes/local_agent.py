"""Local multimodal agent — sees real camera frames and reasons over them.

Unlike the `vision` agent (which delegates to Jetson's Moondream and only gets
back text), local_agent runs on the local multimodal LLM (Gemma via llama.cpp)
and keeps the actual captured frames in its conversation. This makes follow-up
questions about the same scene ("did he wear spectacles?", "is this the real
poster?") reason over the real pixels instead of a one-shot text summary.

It is fed the IMAGE-PRESERVING projection of the shared log (keep_images=True),
and runs on a dedicated llama.cpp slot (see get_llm("local_agent")) so its
cached image prefix is not evicted by other agents' calls.
"""

import logging

from langchain_core.messages import SystemMessage

from ..llm import get_llm
from ..persona import PERSONA
from ..state import AgentState
from ..tools import LOCAL_AGENT_TOOLS
from ..utils.message_utils import prepare_messages_for_agent, safe_invoke

logger = logging.getLogger(__name__)

_PROMPT = PERSONA + """\
Right now you handle visual queries — you can see camera images directly.

== TOOLS ==
  look()                 — capture the current camera view as an image you can see
  handover(next_agent)   — transfer to another agent

== WORKFLOW ==
1. If the user asks about what you can see and you do NOT already have a recent
   camera image in the conversation, call look() first to capture one.
2. For a follow-up about the SAME scene you just looked at (e.g. "did he wear
   spectacles?", "what colour is it?"), reason over the image already in the
   conversation — do NOT call look() again.
3. Call look() again only if the user implies a new or changed view ("look
   again", "what do you see now", "is it still there"), or the last view is stale.
4. Give your answer in your reply text — it is spoken to the user automatically and
   is the ONLY thing said. Don't narrate that you're about to look; just look, then
   describe what you see.
5. Keep answers brief and natural — the user is talking to a physical robot.
6. If the user shifts to navigation, call handover("navigate", reason="navigation").
7. If the user asks about something NOT visual (food/ordering, battery/status,
   general questions, web facts), do NOT try to answer it — call
   handover("supervisor", reason="changed topic") so it is routed correctly.
8. NEVER hand over to "local_agent" (yourself) — look (if needed), then answer.
"""


def local_agent_node(state: AgentState) -> dict:
    llm = get_llm("local_agent").bind_tools(LOCAL_AGENT_TOOLS)
    clean = prepare_messages_for_agent(state["messages"], keep_images=True)
    response = safe_invoke(llm, [SystemMessage(content=_PROMPT)] + clean, logger)
    return {"messages": [response], "active_agent": "local_agent"}
