"""where_is() / list_known_objects() / forget_object() — the robot's spatial
memory, readable in words.

The depth pipeline has always been able to DRIVE the robot to an object, but
nothing could TELL you about one: the map lived inside the approach tools, so
"where's my backpack?" or "how far is the chair?" had no answer and "do you
know where anything is?" required physically turning a full circle
(scan_surroundings). These tools read the same WorldModel the approach tools
navigate with, so what the robot says and what it would drive to can never
disagree.

Answers are phrased for the speaker: a bearing a person can act on ("about two
metres to your left"), not map coordinates.
"""

import logging
import math

from langchain_core.tools import tool

from ..services import world_model
from ._bridge import get as get_bridge

logger = logging.getLogger(__name__)

_LIVE_S = world_model.LIVE_DETECTION_S


def _clock_direction(bearing_deg: float) -> str:
    """Bearing relative to the robot's heading, said the way a person would."""
    b = (bearing_deg + 180.0) % 360.0 - 180.0
    if abs(b) <= 25:
        return "straight ahead"
    if abs(b) >= 155:
        return "behind me"
    side = "left" if b > 0 else "right"
    return f"to my {side}" if abs(b) <= 115 else f"behind me to the {side}"


def _describe(label: str, det: dict, pose: tuple | None) -> str:
    when = det.get("age_phrase", "recently")
    live = det.get("age_s", 0.0) <= _LIVE_S
    if pose is None:
        # No localisation: the position is real but not relatable to "here".
        return (f"I know where the {label} is on my map, but I can't work out "
                f"where I am right now, so I can't tell you which way it is. "
                f"I last saw it {when}.")
    rx, ry, ryaw = pose
    dist = math.hypot(det["x"] - rx, det["y"] - ry)
    bearing = math.degrees(math.atan2(det["y"] - ry, det["x"] - rx)) - ryaw
    where = _clock_direction(bearing)
    seen = "I can see it" if live else f"I last saw it {when}"
    return f"{seen} — about {dist:.1f} metres away, {where}."


@tool
def where_is(object_name: str) -> str:
    """Say where an object is: how far away and in which direction.

    Use for "where's the chair?", "how far is the sofa?", "do you know where my
    backpack is?" — any question ABOUT a location that does not ask the robot
    to move. To actually go there, use approach_object instead.

    object_name: a common object class in lowercase English ('chair', 'person',
    'bottle', 'tv', ...). Reports whether it is visible right now or being
    remembered from an earlier sighting.
    """
    world = world_model.get()
    label = object_name.lower().strip()
    pose = get_bridge().get_current_pose()      # live robot I/O stays on the bridge

    det = None
    if pose is not None:
        # Nearest beats freshest whenever the user could walk to it.
        det = world.nearest(label, pose[0], pose[1])
    if det is None:
        det = world.freshest(label)
    if det is None:
        known = world.labels()
        if known:
            return (f"I've never seen a {label}. I do know where these are: "
                    f"{', '.join(known[:8])}.")
        return (f"I've never seen a {label}, and I haven't mapped anything yet — "
                f"ask me to scan the room first.")

    summary = world.summary(label) or {}
    answer = _describe(label, det, pose)
    count = summary.get("count", 1)
    if count > 1:
        answer += f" There are {count} of them on my map; that's the closest."
    return answer


@tool
def list_known_objects() -> str:
    """List the objects the robot knows the position of, and which are visible
    right now. Use for "what do you know where things are?", "what have you
    mapped?", "can you see anything?"."""
    world = world_model.get()
    known = world.labels()
    if not known:
        return ("I haven't placed anything on my map yet. Ask me to scan the "
                "room and I'll look around.")
    visible = set(world.all_fresh(max_age_s=_LIVE_S))
    now = sorted(visible)
    remembered = [k for k in known if k not in visible]
    parts = []
    if now:
        parts.append("Right now I can see " + ", ".join(now) + ".")
    if remembered:
        parts.append("I also remember where these are: " + ", ".join(remembered) + ".")
    return " ".join(parts)


@tool
def forget_object(object_name: str) -> str:
    """Forget where an object is, when it has been moved or taken away.

    Use when the user says something like "the chair isn't there any more" or
    "I moved the backpack". Without this the robot keeps driving to the last
    place it saw the thing.
    """
    label = object_name.lower().strip()
    gone = world_model.get().forget(label)
    if not gone:
        return f"I didn't have a {label} on my map anyway."
    return f"Forgotten — I'll look for the {label} fresh next time."
