"""Where the robot was when it saw something — the stamps that make a stale
camera view visibly stale.

THE BUG THIS EXISTS FOR. look() used to inject its frame labelled
"[Current camera view]": written once, never rewritten, so four drives later it
still said *Current* and the model answered questions about the surroundings
from a photo of a place the robot had left. The first fix (86e0b08) appended a
sentence to every movement result saying the view was now old. That is an
INSTRUCTION, and it was competing with LOCAL_AGENT_PROMPT's own numbered rule
saying not to look again — a sentence in a ToolMessage does not outrank a rule
in the system prompt, and the note is written during a `navigate` turn while the
question is answered by `local_agent`.

So stop asserting currency and stamp PROVENANCE instead. A camera view carries
the pose it was taken FROM; every user turn carries where the robot is NOW.
Nothing has to expire, because nothing ever claimed to be current — two poses
that disagree are the evidence, and the prompt only has to say "compare them".
That is also how a person knows their mental picture of a room is out of date:
not by being told, but because their body state no longer matches it.

CACHE. Both stamps are plain text at the TAIL of the message list, which is the
one place per-turn state is free (utils/message_utils.py's append-only
invariant). Do NOT move a pose into the system prompt or AgentSpec.context: the
system prompt is the cached PREFIX, and per-turn state there re-prefills the
whole conversation every turn, on every agent — registry.py explains why only
the date is allowed to live there.

STATE. The last look()'s pose lives at module level, the same way _bridge.py
holds the ROS adapter: look() and agent_node run in the same process, and a
module attribute keeps StubBridge and ROS2Bridge identical for free instead of
being a field two classes have to remember to mirror (the trap b2bf231 hit with
ground_pixel).

FRAMES. The pose is whatever bridge.get_current_pose() returns — odom, whose
origin is wherever this power cycle started. That makes a stamp comparable only
WITHIN one boot, which is all this is for. Cross-session place memory needs
relocalization (Phase 2c) and is a different problem.
"""

import math
import time

# The label every camera frame in the conversation starts with. utils/history
# imports it to recognise a look()-injected frame (it must never cut history at
# one, and it evicts old ones on a trim). Deliberately has no closing bracket:
# the stamp is appended and the bracket closed by view_label().
CAMERA_VIEW_MARKER = "[Camera view"

# Below these, the robot has not meaningfully moved: odom jitter, wheel
# settling, and the sub-centimetre drift of standing still. Above either, the
# photo is of somewhere else. Chosen against the rover's measured turn variance
# (±20–25% spin-down coast, Todays_Todo.md) — anything tighter would call a
# stationary robot moved every turn.
MOVED_M = 0.15
TURNED_DEG = 15.0

# Pose (x, y, yaw_deg) and wall-clock of the most recent look() capture.
_last_view_pose = None
_last_view_time = None


def record_view(pose, when=None) -> None:
    """Remember where and when look() captured a frame.

    Called ONLY on a successful capture: a failed look() leaves no photo in the
    conversation, so there is nothing for a later turn to be stale against.
    """
    global _last_view_pose, _last_view_time
    _last_view_pose = tuple(pose) if pose else None
    _last_view_time = time.time() if when is None else when


def forget_view() -> None:
    """Drop the remembered view. For tests, and for a history reset that takes
    the frame with it."""
    global _last_view_pose, _last_view_time
    _last_view_pose = None
    _last_view_time = None


def last_view():
    """(pose, when) of the last look(), either of which may be None."""
    return _last_view_pose, _last_view_time


def _wrap(deg: float) -> float:
    """Fold an angle into (-180, 180], so 359° and 1° are 2° apart."""
    return (float(deg) + 180.0) % 360.0 - 180.0


def format_pose(pose) -> str | None:
    """"x=1.20 y=0.34 heading=45°", or None when there is no pose.

    None is not an error: the real bridge returns None when TF has no
    odom->base_link fix, and every caller here is required to say so rather than
    invent a position.
    """
    if not pose:
        return None
    x, y, yaw = pose
    return f"x={x:.2f} y={y:.2f} heading={_wrap(yaw):.0f}\N{DEGREE SIGN}"


def delta(a, b):
    """(metres, degrees) between two poses, or None if either is missing."""
    if not a or not b:
        return None
    return math.hypot(b[0] - a[0], b[1] - a[1]), abs(_wrap(b[2] - a[2]))


def has_moved(a, b) -> bool:
    """True if the robot is somewhere a photo taken at `a` no longer shows."""
    d = delta(a, b)
    return d is not None and (d[0] > MOVED_M or d[1] > TURNED_DEG)


def describe_delta(a, b) -> str | None:
    """"0.90 m and 180°" — how far off a stamp is. None if either pose is missing."""
    d = delta(a, b)
    if d is None:
        return None
    metres, degrees = d
    parts = []
    if metres >= 0.01:
        parts.append(f"{metres:.2f} m")
    if degrees >= 1.0:
        parts.append(f"{degrees:.0f}\N{DEGREE SIGN}")
    return " and ".join(parts) if parts else None


def view_label(pose, when=None) -> str:
    """The text look() writes beside its frame. It never says "current"."""
    clock = time.strftime("%H:%M:%S", time.localtime(when or time.time()))
    where = format_pose(pose)
    if where is None:
        return f"{CAMERA_VIEW_MARKER} — taken at {clock}, robot position unknown]"
    return f"{CAMERA_VIEW_MARKER} — taken at {clock} from {where}]"


def turn_stamp(now_pose) -> str | None:
    """The body-state prefix on a user turn, or None if there is nothing to say.

    Returns None until a look() has happened. Before the first frame there is
    nothing to compare against, and stamping "what's the weather" with a pose is
    noise the model has to read past on every text turn.
    """
    if _last_view_pose is None and _last_view_time is None:
        return None

    where = format_pose(now_pose)
    if where is None:
        # Localisation is gone. Say that, rather than let a photo stamped with a
        # pose sit unchallenged next to a turn that carries none.
        return ("[Robot position unknown — it may have moved since the last "
                "camera view was taken]")

    moved = describe_delta(_last_view_pose, now_pose)
    if not has_moved(_last_view_pose, now_pose):
        return f"[Robot now at {where} — unmoved since the last camera view]"
    return (f"[Robot now at {where} — that is {moved} from where the last camera "
            f"view was taken, so that photo shows somewhere it has left]")
