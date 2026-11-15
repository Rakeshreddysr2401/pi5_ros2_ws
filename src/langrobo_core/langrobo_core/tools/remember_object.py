"""remember_object -- "remember the blue bottle": where a thing in front of the robot IS.

Owner, 2026-11-16: in front of a blue bottle, "remember the blue bottle" must save its
location, and "go to the blue bottle" must go there later -- also after a power-off.
save_location keeps where the ROBOT stands; this keeps where the THING is, measured:

    photo now ──▶ vision model: where is <description> ──▶ box
        └▶ Jetson: that photo's depth + pose ──▶ (x, y) in odom ──▶ object memory, PINNED
           (never ages out; kept in the house map across power-off: services/object_memory.py)

"Go to the blue bottle" then finds it (approach: photos, then this memory) and still
LOOKS before going near: things move. It does not move the robot.
"""

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..services import object_memory
from . import _bridge
from . import approach as _ap


@tool
def remember_object(description: str,
                    state: Annotated[dict | None, InjectedState] = None) -> str:
    """Remember where a THING in front of the robot is, so the robot can go to it later
    -- also after a restart. Use when the user says "remember the blue bottle",
    "remember where my bag is", "this is the dining table, remember it". It looks with
    the camera and measures the spot; it does not move the robot. (To save where the
    ROBOT stands, use save_location.)"""
    description = (description or "").strip()
    if not description:
        return "Ask what to remember."
    name = _ap._short_name(description)
    bridge = _bridge.get()
    frame, capture = _ap._capture(bridge, settle_s=1.0, source="remember")
    if frame is None:
        return "My camera isn't giving me a fresh image right now, so I can't see it to remember it."
    try:
        uv = _ap._vlm_locate(frame, description)
    except Exception as e:                                  # noqa: BLE001
        return f"I couldn't look at the camera image (vision model error: {type(e).__name__}). Try again."
    if uv is None:
        return (f"I don't see the {name} in front of me. Turn me towards it (or move it into "
                f"view) and ask again.")
    res = _ap._ground(bridge, uv, capture, what=description)
    if not res.get("ok"):
        why = _ap.DEPTH_FAIL_HELP.get(res.get("reason"), res.get("reason") or "no depth")
        if isinstance(why, tuple):
            why = " ".join(why)
        return f"I can see the {name} but couldn't measure where it is: {why}."
    obj = res.get("object") or {}
    if "x" not in obj or "y" not in obj:
        return f"I can see the {name} but couldn't place it in the room."
    pose = (capture or {}).get("pose") or bridge.get_current_pose()
    object_memory.remember(description, obj["x"], obj["y"], pose, bridge.get_origin_epoch(),
                           pinned=True, source="remember", depth_m=res.get("depth_m"),
                           photo=list(capture["stamp"]) if (capture or {}).get("stamp") else None)
    far = res.get("depth_m")
    where = f", about {far:.1f} m ahead" if isinstance(far, (int, float)) else ""
    return (f"Got it -- I'll remember the {name}{where}. Ask me to go to the {name} any "
            f"time, even after a restart.")
