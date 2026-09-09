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
from .approach import _fresh_frame, _vlm_locate

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


# Depth failures the Jetson can report, in language a user can act on. Every
# one of these means "the pixel was found but the sensor could not measure it",
# which is a different answer from "I cannot see it" and must not be collapsed
# into one — the object IS there.
_DEPTH_FAIL_HELP = {
    "no_depth_at_pixel": ("the depth sensor has no reading there — it may be "
                          "glass, a mirror, something very dark, or an edge"),
    "depth_out_of_range": ("it is outside the depth camera's usable range — "
                           "closer than 0.3 m or too far away"),
    "no_depth_frame": "the depth stream has stopped",
    "no_camera_info": "the camera calibration is not being published",
    "pixel_out_of_bounds": "I picked a point outside the image",
    "no_robot_pose": "I do not currently know where I am",
    "no_reply_from_jetson": ("the depth service on the Jetson is not "
                             "answering"),
}


@tool
def locate_object(description: str,
                  state: Annotated[dict, InjectedState] = None) -> str:
    """Measure how far away something is and which direction it is in, using
    the depth camera. Use for "how far is the chair", "where is the bottle",
    "how close am I to the wall", or whenever a real distance is wanted.

    Returns a measured distance in metres, a direction, and coordinates. Does
    NOT move the robot and does not drive to the object — use
    approach_described_object for that.

    Only sees what is in the current camera view; it will not turn to search.
    Never state a distance that did not come from this tool: the camera image
    alone cannot tell you how far away anything is.
    """
    bridge = _bridge.get()

    frame = _fresh_frame(bridge)
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
                f"me to search.")

    res = bridge.ground_pixel(*uv)
    if not res.get("ok"):
        reason = res.get("reason", "unknown")
        help_text = _DEPTH_FAIL_HELP.get(reason, f"depth reading failed: {reason}")
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

    # Everything here is robot-relative and stays that way. The odom position
    # is deliberately NOT reported: odom's origin is wherever the rover was
    # when ./rover fused started and bears no relation to where it is pointing
    # now, so "y=0.75" says nothing about left or right -- but printed beside
    # forward/left, which DO, it reads as though it does. A user read it that
    # way on 2026-09-10 and reasonably concluded the tool was wrong about a
    # wall it had in fact located correctly. A number nobody can act on is not
    # worth the sentence it costs, let alone the contradiction.
    return (f"{description.capitalize()} is about {dist:.1f} m away, "
            f"{where} ({rel['bearing_deg']:+.0f}°). "
            f"That's {rel['forward_m']:.2f} m in front of me and "
            f"{abs(rel['left_m']):.2f} m to my "
            f"{'left' if rel['left_m'] >= 0 else 'right'} "
            f"— left and right from the robot's own point of view, so they are "
            f"mirrored if you are facing it.")
