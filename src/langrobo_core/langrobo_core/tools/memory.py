"""recall_object() — "where did you see my bottle?", from memory, without moving.

The read side of services/object_memory (rover repo INTELLIGENCE_PLAN.md B3).
Every successful locate_object / approach_described_object writes the object's
position in odom; this answers from those entries, measured from where the
robot is NOW -- the owner's case: seen from position 1, asked about at
position 2.

It never claims the object is still there. An entry is what the camera saw
then; saying so plainly, with its age, is the honest answer. Checking it is a
look (local_agent) or approach_described_object, which re-checks before it
drives.
"""

import time

from langchain_core.tools import tool

from ..services import object_memory
from . import _bridge


def describe_remembered(bridge, description: str) -> str | None:
    """One sentence about the best remembered match, or None."""
    try:
        found = object_memory.recall(description, bridge.get_origin_epoch())
    except OSError:
        return None
    if not found:
        return None
    return _sentence(found[0], bridge.get_current_pose())


def _sentence(e: dict, pose) -> str:
    from .locate import describe_bearing      # here, not at the top: locate imports this module
    age = object_memory.describe_age(time.time() - e.get("seen_at", 0))
    rel = object_memory.relative(e, pose)
    if rel is None:
        return (f"I saw {e['description']} {age}, but I don't know where I am right "
                f"now, so I can't say which way it is.")
    dist, bearing = rel
    return (f"I saw {e['description']} {age}: from where I am now it would be "
            f"{dist:.1f} m away, {describe_bearing(bearing)} ({bearing:+.0f}°). "
            f"I haven't checked it is still there.")


@tool
def recall_object(description: str = "") -> str:
    """Say where the robot last saw something this session, from memory:
    distance and direction from where the robot is NOW, and how long ago.
    Use for "where did you see my bottle?", "where was the chair?", "what have
    you seen?" (empty description = everything remembered).

    Does NOT move or look. The answer is what the camera saw then -- it may
    have been moved since. To check, look again or approach it
    (approach_described_object re-checks before it drives)."""
    bridge = _bridge.get()
    try:
        found = object_memory.recall(description, bridge.get_origin_epoch())
    except OSError:
        found = []
    if not found:
        what = description.strip() or "anything"
        return (f"I don't remember seeing {what} since I started up. Things I have "
                f"measured with the camera are remembered; ask me to look for it.")
    pose = bridge.get_current_pose()
    if description.strip():
        return _sentence(found[0], pose)
    return " ".join(_sentence(e, pose) for e in found[:6])
