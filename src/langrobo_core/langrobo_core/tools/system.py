"""Robot introspection — the clock and the hardware state. Pure zone."""

from datetime import datetime

from langchain_core.tools import tool

from . import _bridge


# A tool rather than a line in the system prompt, on purpose: a clock baked
# into a prompt changes every minute, which changes the cached prefix, which
# re-prefills ~2k tokens on every minute tick (~20s on the 12B model). See
# prompts.py's header. That reasoning lives HERE, in a comment — a tool's
# docstring is prompt text, shipped to the model on every single turn, so it
# gets the instruction and nothing else.
@tool
def get_current_time() -> str:
    """Get the current date and time. Call it whenever the answer depends on
    the clock — the prompt carries today's date but never the time."""
    now = datetime.now()
    return now.strftime("%A, %B %d, %Y at %I:%M %p").replace(" 0", " ")


@tool
def get_robot_status() -> str:
    """Get the robot's current hardware state — whether the camera feed is
    alive and which body the robot is driving. Use for "how are you doing?",
    "are you okay?", "is your camera working?".

    Deliberately reports only what this machine can actually observe. There is
    no battery reading: nothing on the rover publishes one (the ESP32 firmware
    has three subscriptions and none of them are power). The old version called
    a /robot/get_status service that no node provides, so it timed out on every
    call and answered "operational" regardless — which is worse than silence."""
    bridge = _bridge.get()
    parts = []

    age = bridge.frame_age()
    if age is None:
        parts.append("my camera hasn't sent a frame at all")
    elif age > 10.0:
        parts.append(f"my camera feed is stale ({age:.0f} seconds old)")
    else:
        parts.append("my camera feed is live")

    parts.append(f"I'm driving the {bridge.robot_body} body")
    return "Status: " + ", ".join(parts) + "."
