"""look() — capture the current camera frame into the conversation as an image.

Tool-driven capture: the model decides when to look.  The captured frame is
injected as a HumanMessage image block (NOT a ToolMessage — OpenAI-compatible
servers, including llama.cpp, only honour images in user-role messages), so it
persists in the conversation and stays available for follow-up questions about
the same scene without re-querying.

THAT REUSE IS ONLY VALID WHILE THE ROBOT HAS NOT MOVED, and the frame's label
is what says so. It used to read "[Current camera view]" — written once, never
rewritten, so after a drive the model was answering "what is in front of you"
from a photo of somewhere the robot no longer is, while the label still claimed
the photo was current.

The label now carries the POSE THE FRAME WAS TAKEN FROM instead of an assertion
that it is current (utils/pose_stamp), and every user turn carries where the
robot is now. Nothing has to expire a stamp: two poses that disagree are the
evidence. Tools that move the base still append a note as a second signal — see
movement.view_stale_note.
"""

import base64
import time
from typing import Annotated

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

from ..utils import pose_stamp
from ._bridge import get


@tool
def look(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Capture the current camera view so you can see and reason about it.

    Call this when you need fresh visual information and don't already have a
    recent frame in the conversation. The captured image is added to the
    conversation and remains available for follow-up questions about the same
    scene, so you do NOT need to call look() again for a follow-up about the
    SAME view.

    Each captured view is labelled with the pose it was taken FROM, and each
    turn is labelled with where the robot is NOW. If those two poses differ the
    robot has moved, the earlier photo shows a place it has left, and you MUST
    call look() again before saying anything about the surroundings.
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

    # Stamp the frame with where it was taken from, and remember that pose so
    # the next turn can say how far the robot has moved from it. Recorded only
    # on a SUCCESSFUL capture: a failed look() puts no photo in the
    # conversation, so there is nothing for a later turn to be stale against.
    pose = bridge.get_current_pose()
    when = time.time()
    pose_stamp.record_view(pose, when)

    b64 = base64.b64encode(frame).decode()
    return Command(update={"messages": [
        ToolMessage("Captured the current camera view.", tool_call_id=tool_call_id),
        HumanMessage(content=[
            {"type": "text", "text": pose_stamp.view_label(pose, when)},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]),
    ]})
