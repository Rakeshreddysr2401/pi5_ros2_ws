"""look() — capture the current camera frame into the conversation as an image.

Tool-driven capture: the model decides when to look.  The captured frame is
injected as a HumanMessage image block (NOT a ToolMessage — OpenAI-compatible
servers, including llama.cpp, only honour images in user-role messages), so it
persists in the conversation and stays available for follow-up questions about
the same scene without re-querying.
"""

import base64
from typing import Annotated

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

from ._bridge import get


@tool
def look(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Capture the current camera view so you can see and reason about it.

    Call this when you need fresh visual information and don't already have a
    recent frame in the conversation. The captured image is added to the
    conversation and remains available for follow-up questions about the same
    scene, so you do NOT need to call look() again for follow-ups unless the
    scene may have changed.
    """
    bridge = get()
    frame = bridge.get_frame()
    if frame is None:
        return Command(update={"messages": [
            ToolMessage(
                "No camera frame is available right now — the camera may be off.",
                tool_call_id=tool_call_id,
            )
        ]})

    b64 = base64.b64encode(frame).decode()
    return Command(update={"messages": [
        ToolMessage("Captured the current camera view.", tool_call_id=tool_call_id),
        HumanMessage(content=[
            {"type": "text", "text": "[Current camera view]"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]),
    ]})
