"""locate_object() — "how far away is the chair?", answered with real depth.

The measuring counterpart to look(). look() puts the camera frame into the
conversation and the model describes it; it has RGB pixels and nothing else, so
any distance it offers is invented. This tool asks the same VLM only WHICH
PIXEL the object is at, then has the Jetson read the actual depth there:

    look frame ──▶ VLM: "where is the chair?" ──▶ pixel (u, v)
                                                     │
                          Jetson pixel_to_goal: depth + intrinsics + TF ─┘
                                                     ▼
                              range, bearing, and odom coordinates

Semantics from the language model, metrics from the depth sensor. The VLM is
never asked for a number, because it cannot know one — a depth raster does not
survive JPEG encoding as a metric, and patch embeddings do not preserve pixel
values for readout.

IT DOES NOT MOVE THE ROBOT. approach_described_object rotates up to a full
circle hunting for its target (_SEARCH_STEPS); that is right when the user
asked to go somewhere and wrong when they asked a question. If the object is
not in the current view this says so and stops. Answering "it is behind me"
without spinning is a better answer than spinning uninvited.

Geometry all comes from the Jetson's reply — see pixel_to_goal.py's CONTRACT.
Notably it reports `relative`, measured from base_link, NOT `goal`, which is
0.45 m short of the object on purpose so nav2 parks in front of it.
"""

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from . import _bridge
from .approach import DEPTH_FAIL_HELP, _capture, _ground_retrying, _vlm_locate

# Turned into "to my left" / "ahead" for speech. The VLM's pixel is itself only
# good to a few degrees and ground_pixel medians over a window, so finer
# gradations than these would be false precision.
_BEARING_BANDS = [
    (10.0, "straight ahead"),
    (35.0, "slightly to my {side}"),
    (80.0, "to my {side}"),
    (180.0, "far to my {side}"),
]


def describe_bearing(bearing_deg: float) -> str:
    """Bearing in degrees (0 ahead, + counter-clockwise/left) -> a phrase."""
    side = "left" if bearing_deg >= 0 else "right"
    mag = abs(bearing_deg)
    for limit, phrase in _BEARING_BANDS:
        if mag <= limit:
            return phrase.format(side=side)
    return _BEARING_BANDS[-1][1].format(side=side)


@tool
def locate_object(description: str,
                  state: Annotated[dict, InjectedState] = None) -> str:
    """Measure how far away something in the current camera view is, and which way, with the depth camera ("how far is the chair?").

    Returns metres, a direction, and coordinates relative to the robot. Does
    NOT move or turn: only what is in view now; to drive there, use
    approach_described_object. Never state a distance that did not come from
    this tool. For the distance BETWEEN two objects, call it once for each and
    compute sqrt((x1-x2)^2 + (y1-y2)^2) from the two coordinate pairs.
    """
    # The long version of the rules above, kept here and not in the schema
    # (which is sent on every local_agent turn): the coordinates are measured
    # values, so the gap between two objects is valid arithmetic -- on
    # 2026-09-10 the robot answered "I cannot tell you the distance between
    # them" while holding both pairs (they were 1.05 m apart).
    bridge = _bridge.get()
    # agent_node raises the motion-stop flag on every utterance (so a new
    # command halts a hunt in progress), and _capture bails out while it is
    # up. Without this, the very utterance that asked "how far is the chair?"
    # made its own measurement fail in 13 ms as "no fresh image" -- on every
    # voice turn (2026-09-27). The motion tools clear it the same way.
    bridge.clear_motion_stop()

    frame, capture = _capture(bridge, source="locate")
    if frame is None:
        return ("My camera feed isn't giving me a fresh image right now, so I "
                "can't measure anything.")

    try:
        uv = _vlm_locate(frame, description)
    except Exception as e:
        return (f"I couldn't analyse the camera image (vision model error: "
                f"{type(e).__name__}). Try again in a moment.")

    if uv is None:
        return (f"I can't see {description} in my current view. I haven't "
                f"turned to look around — ask me to look for it if you want "
                f"me to search, or ask where I saw it before.")

    res, capture = _ground_retrying(bridge, description, uv, capture)   # at the moment of the photo
    if not res.get("ok"):
        reason = res.get("reason", "unknown")
        help_text = DEPTH_FAIL_HELP.get(reason, f"depth reading failed: {reason}")
        return (f"I can see {description}, but I can't measure its distance — "
                f"{help_text}.")

    rel = res.get("relative")
    if not rel:
        # Jetson still on the pre-2026-09-10 contract. depth_m is the camera's
        # Z-forward component, so it is a real measurement and worth giving —
        # but say where it is measured from rather than implying base_link.
        return (f"{description.capitalize()} is about {res['depth_m']:.1f} m "
                f"from my camera. (My Jetson is running an older depth service "
                f"that can't give me the direction or coordinates.)")

    dist = (rel["forward_m"] ** 2 + rel["left_m"] ** 2) ** 0.5
    where = describe_bearing(rel["bearing_deg"])
    fwd, lft = rel["forward_m"], rel["left_m"]

    # Coordinates, said as coordinates. The robot frame (REP-103): +x forward,
    # +y left, origin at base_link. These are the same two numbers as the plain
    # sentence below and are given both ways on purpose -- "x +1.27, y +0.76"
    # is what gets asked for, "1.27 m in front of me" is what gets understood
    # when it is read aloud over Telegram or TTS.
    #
    # Robot frame, NOT odom. odom's origin is wherever ./rover fused started
    # and says nothing about left or right; printing it beside these misled a
    # user on 2026-09-10 into reading a correct answer as wrong.
    return (
        f"{description.capitalize()}: {dist:.2f} m away, {where} "
        f"({rel['bearing_deg']:+.0f}°).\n"
        f"Coordinates relative to me: x {fwd:+.2f} m, y {lft:+.2f} m "
        f"(+x forward, +y left, measured from the robot's centre).\n"
        f"In words: {abs(fwd):.2f} m "
        f"{'in front of' if fwd >= 0 else 'behind'} me and {abs(lft):.2f} m to "
        f"my {'left' if lft >= 0 else 'right'} — left/right are the robot's "
        f"own, so they swap if you are facing it."
    )
