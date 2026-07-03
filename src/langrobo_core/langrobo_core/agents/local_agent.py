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

from ..services.llm import get_llm
from .persona import PERSONA
from ..graph.state import AgentState
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
7. If the user asks something with NO visual part (battery/status, general
   questions, web facts), do NOT try to answer it — call
   handover("supervisor", reason="changed topic") so it is routed correctly.
8. VISION → ACTION: if the user wants another agent to ACT on what you see
   (e.g. "order this", "look at this and order it", "remember what's on the shelf"):
   a. look() and identify the object.
   b. CONFIRM with the user first — name exactly what you identified and ask,
      e.g. "I can see a red apple — you want me to order that, right?". Your
      reply ends the turn; the user's answer comes back to you.
   c. If the user corrects you ("no, the bottle next to it"), check the image
      again (or look() afresh) and re-confirm the corrected object.
   d. Only AFTER the user confirms, hand over with EVERY needed visual detail
      spelled out in the reason — other agents CANNOT see images, so your
      reason text is the only visual information they get.
      Example: handover("swiggy", reason="user confirmed: order 3 ripe bananas like the ones on their shelf").
   Skip the confirmation only when there is nothing to disambiguate (the user
   already named the item and you are just adding visual detail).
9. If a routing note relays a visual question from another agent, look (if
   needed) and hand back to THAT agent with the answer in the reason.
10. NEVER hand over to "local_agent" (yourself) — look (if needed), then answer.
"""


def build_llm_call(messages: list):
    """Return (llm, prompt_messages) for a local_agent turn.

    Shared by local_agent_node and agent_node's cache warmer — identical bound
    tools and prompt so the warmed slot-1 prefix (including camera frames)
    matches the next real request byte-for-byte.
    """
    llm = get_llm("local_agent").bind_tools(LOCAL_AGENT_TOOLS)
    clean = prepare_messages_for_agent(messages, keep_images=True)
    return llm, [SystemMessage(content=_PROMPT)] + clean


def local_agent_node(state: AgentState) -> dict:
    llm, msgs = build_llm_call(state["messages"])
    response = safe_invoke(llm, msgs, logger)
    return {"messages": [response], "active_agent": "local_agent"}
