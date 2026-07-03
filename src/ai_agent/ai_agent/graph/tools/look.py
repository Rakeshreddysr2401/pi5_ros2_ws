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
    # Reject frames older than this: the camera publishes continuously, so a
    # stale cache means the camera node or the Jetson link is down — describing
    # a long-gone scene as "current" is worse than admitting blindness.
    frame = bridge.get_frame(max_age_s=10.0)
    if frame is None:
        age = bridge.frame_age()
        detail = (
            f"the last frame is {age:.0f}s old — the camera feed appears to be down"
            if age is not None else "the camera may be off"
        )
        return Command(update={"messages": [
            ToolMessage(
                f"No current camera frame is available ({detail}). "
                "Tell the user you cannot see right now.",
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
