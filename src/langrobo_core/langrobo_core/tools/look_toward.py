"""look_toward(thing) — turn to where something was seen, then look at it NOW.

Real robot, 2026-10-18: "how many books are on the shelf?" with the shelf
BEHIND the robot. local_agent can only see ahead, so it answered from an old
photo after 24 s -- "several books" -- without turning or counting. A full
look-around (scan_surroundings, ~45 s and five pivots) is the wrong tool for
one question about one known thing.

This one: where is it? (object memory first -- free; else the photo log, one
vision call on the vision-tool slot) -> turn in place to face it (skipped
within ~20 deg: the camera sees 87 across) -> a fresh photo straight into the
conversation, exactly as look() puts one. A plain direction works too:
"behind you", "to your left".

It turns the base, so it is a MOTION tool (role and teleop-MANUAL checks, and
movement.MOTION_TOOL_NAMES). It never drives: a turn in place only.
"""

import logging
import re
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ..services import object_memory
from . import _bridge
from . import movement as _mv
from . import photo_recall as _recall
from .look import look

logger = logging.getLogger(__name__)

FACE_WITHIN_DEG = 20.0          # already this close: no turn, just look

_DIRECTIONS = (
    (r"\bbehind\b|\bback\b|\bturn around\b", 180.0),
    (r"\bleft\b", 90.0),
    (r"\bright\b", -90.0),
    (r"\bahead\b|\bin front\b|\bforward\b", 0.0),
)


def _direction(text: str) -> float | None:
    t = text.lower()
    for pat, deg in _DIRECTIONS:
        if re.search(pat, t):
            return deg
    return None


def where(description: str) -> tuple[float | None, str]:
    """(bearing_deg from the robot now, + left | None, where it came from)."""
    d = _direction(description)
    if d is not None and len(description.split()) <= 4:     # "behind you", "to your left"
        return d, "direction"
    bridge = _bridge.get()
    pose, epoch = bridge.get_current_pose(), bridge.get_origin_epoch()
    if pose is not None and epoch is not None:
        hits = object_memory.recall(description, epoch)
        if hits:
            rel = object_memory.relative(hits[0], pose)
            if rel:
                return rel[1], "memory"
    try:
        r = _recall.ask(description)
    except Exception as e:                                  # noqa: BLE001
        logger.warning("look_toward: photo recall failed: %s", e)
        return None, "error"
    if r.get("ok") and r.get("bearing_deg") is not None:
        return r["bearing_deg"], "photos"
    return None, "unknown"


@tool
def look_toward(description: str,
                tool_call_id: Annotated[str, InjectedToolCallId],
                state: Annotated[dict | None, InjectedState] = None) -> Command:
    """Turn to face something you saw earlier (or a direction such as "behind you") and look at it now.
    Use when the question is about something NOT in your current view: "how many
    books are on the shelf?" with the shelf behind you, "is the door still open?",
    "what is behind you?". Turns in place only, then you see a fresh photo."""
    def reply(text):
        return Command(update={"messages": [ToolMessage(text, tool_call_id=tool_call_id)]})

    refusal = _mv.blocked_by_role(state) or _mv.blocked_by_manual()
    if refusal:
        return reply(refusal)
    bearing, source = where(description)
    if bearing is None:
        return reply(f"I don't know where {description} is: it is not in my memory or in any photo "
                     f"I took. Tell the user that; offer to look around the room.")
    bridge = _bridge.get()
    bridge.clear_motion_stop()
    turned = ""
    if abs(bearing) > FACE_WITHIN_DEG:
        ok, why = _mv.turn_robot(bridge, bearing)
        if not ok:
            if why == "interrupted":
                return reply("Stopped.")
            return reply(f"I could not turn toward {description} ({why}). Tell the user; "
                         f"I did not get a new look at it.")
        turned = f"Turned {abs(round(bearing))} degrees {'left' if bearing > 0 else 'right'} to face {description}. "
    seen = look.func(tool_call_id=tool_call_id)
    msgs = list(seen.update["messages"])
    first = msgs[0]
    msgs[0] = ToolMessage(turned + str(first.content), tool_call_id=tool_call_id)
    return Command(update={"messages": msgs})
