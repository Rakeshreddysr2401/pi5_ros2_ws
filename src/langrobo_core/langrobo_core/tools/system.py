"""Robot introspection — the clock and the hardware state. Pure zone."""

import math
import time
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


# Reports only what this machine can actually observe. There is no battery
# reading: nothing on the rover publishes one (the ESP32 firmware has three
# subscriptions and none of them are power). The old version called a
# /robot/get_status service that no node provides, so it timed out on every
# call and answered "operational" regardless — which is worse than silence.
#
# Where the robot is and whether it is still driving come from the bridge, not
# from the conversation: on 2026-10-04 "where are you now?" got "1.5 m from the
# door" -- the drive's opening line -- after it had arrived, and "check your
# location again" got a STOP, because nothing could answer the question.
@tool
def get_robot_status() -> str:
    """Where the robot is right now, whether it is still driving (and to where,
    how far to go) or how its last drive ended, and whether the camera works.
    Use for "where are you?", "did you get there?", "are you still going?",
    "how are you doing?". It is the only true answer to those -- never answer
    them from earlier messages."""
    bridge = _bridge.get()
    parts = []

    pose = bridge.get_current_pose()
    if pose:
        parts.append(f"I'm at x={pose[0]:.2f} m, y={pose[1]:.2f} m, facing "
                     f"{pose[2]:.0f} deg (measured from where I started this session)")
    else:
        parts.append("I don't know where I am right now (no pose)")
    parts.append(_drive_status(bridge.navigation_state(), pose))

    age = bridge.frame_age()
    if age is None:
        parts.append("my camera hasn't sent a frame at all")
    elif age > 10.0:
        parts.append(f"my camera feed is stale ({age:.0f} seconds old)")
    else:
        parts.append("my camera feed is live")

    parts.append(f"I'm driving the {bridge.robot_body} body")
    return "Status: " + "; ".join(parts) + "."


def _drive_status(nav: dict, pose: tuple | None) -> str:
    goal, last = nav.get("goal") or {}, nav.get("last")
    if nav.get("active"):
        where = f"'{goal['label']}'" if goal.get("label") else "a goal"
        togo = ""
        if pose and "x" in goal:
            togo = f", {math.hypot(goal['x'] - pose[0], goal['y'] - pose[1]):.1f} m to go"
        mins = (time.time() - goal.get("since", time.time())) / 60.0
        return f"I'm still driving to {where} ({mins:.0f} min so far{togo})"
    if not last:
        return "I haven't driven anywhere this session"
    where = f" to '{last['label']}'" if last.get("label") else ""
    ago = _ago(time.time() - last["at"])
    if last["success"]:
        return f"I'm not driving; my last drive{where} ARRIVED {ago}"
    return f"I'm not driving; my last drive{where} FAILED {ago}: {last['message']}"


def _ago(secs: float) -> str:
    return f"{secs:.0f} s ago" if secs < 90 else f"{secs / 60:.0f} min ago"
