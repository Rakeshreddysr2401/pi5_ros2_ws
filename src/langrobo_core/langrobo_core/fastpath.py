"""Deterministic movement fast-path — spoken command to wheels with NO LLM.

Movement is the robot's top-priority function (New_Requirement.md): a
round-trip through chat + navigate on the 12B model costs seconds even
with hot KV slots. This lane recognises exact movement phrasings with strict
regexes and executes the SAME tools the navigate agent would call, in
milliseconds. Anything the matcher is not certain about returns None and
flows through the normal graph — the LLM stays the fallback for paraphrases,
compound commands and context-dependent requests.

Contract with agent_node:
  - try_handle(text) → the full spoken reply (for history) or None (no match).
  - It replies by itself through say_fn (ack BEFORE slow actions, result
    after) — the speaker for voice turns, the sender's chat for Telegram —
    so the user gets an instant response while the robot moves.
  - Voice and text-only Telegram turns; system and photo turns take the graph.

Pure zone: no rclpy; robot I/O via tools + _bridge.get(). match() is a pure
function of (text, known_locations) and is unit-tested exhaustively.
"""

import logging
import re
from dataclasses import dataclass, field

from .tools import _bridge

logger = logging.getLogger(__name__)

# ── Vocabulary ────────────────────────────────────────────────────────────────

# COCO classes the Jetson detector can actually find, plus spoken aliases.
COCO_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "bus", "train", "truck",
    "bench", "bird", "cat", "dog", "backpack", "umbrella", "handbag",
    "suitcase", "bottle", "wine glass", "cup", "fork", "knife", "spoon",
    "bowl", "banana", "apple", "orange", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
}
OBJECT_ALIASES = {
    "sofa": "couch", "television": "tv", "telly": "tv", "screen": "tv",
    "phone": "cell phone", "mobile": "cell phone", "table": "dining table",
    "plant": "potted plant", "fridge": "refrigerator", "teddy": "teddy bear",
    "cycle": "bicycle", "bike": "bicycle", "glass": "wine glass",
}

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "half": 0.5,
}

# Leading wake-word / politeness fillers STT commonly prepends.
_LEAD_FILLER = re.compile(
    r"^(?:(?:hey|hi|ok|okay)[\s,]+)?(?:rakhi[\s,]+)?(?:please[\s,]+)?"
    r"(?:can you\s+|could you\s+|would you\s+|will you\s+)?")
_TRAIL_FILLER = re.compile(r"[\s,]*(?:please|now|for me)$")


@dataclass
class FastIntent:
    kind: str                       # stop|move|turn|goto|approach|save|look|scan|list
    args: dict = field(default_factory=dict)


def _normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[.!?]+$", "", t).strip()
    t = _LEAD_FILLER.sub("", t)
    t = _TRAIL_FILLER.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    for word, num in _WORD_NUMBERS.items():
        t = re.sub(rf"\b{word}\b", str(num), t)
    return t


def _canon_object(name: str) -> str | None:
    name = re.sub(r"^(?:the|a|an|my|that|this)\s+", "", name.strip())
    name = OBJECT_ALIASES.get(name, name)
    return name if name in COCO_CLASSES else None


def _loc_key(name: str) -> str:
    return name.strip().replace(" ", "_")


# ── Patterns (all applied to the normalized string, fullmatch only) ───────────

_STOP = re.compile(
    r"(?:stop|halt|freeze|stand still|don'?t move|stay (?:there|here|still)|"
    r"stop (?:moving|there|right there|it|now))")
_MOVE = re.compile(
    r"(?:go|move|drive|come|step)?\s*"
    r"(?P<dir>forwards?|ahead|straight|back|backwards?|reverse)"
    r"(?:\s+(?:by\s+)?(?P<val>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>cm|centimet(?:er|re)s?|m|met(?:er|re)s?)?)?"
    r"(?P<abit>\s+a\s+(?:bit|little))?")
_TURN = re.compile(
    r"(?:turn|rotate|spin)\s*(?P<dir>left|right|around)"
    r"(?:\s+(?:by\s+)?(?P<val>\d+(?:\.\d+)?)\s*(?:degrees?|deg)?)?")
_GOTO = re.compile(
    r"(?:go|navigate|head|drive|walk|move)\s+"
    r"(?:back\s+)?(?:to|into|near|towards?)\s+(?:the\s+|my\s+)?(?P<name>.+)")
_COME = re.compile(
    r"come(?:\s+(?:here|to me|near me|over here|closer|on over|to us))?|find me|come find me")
_FIND = re.compile(
    r"(?:find|locate|look for|approach|go find)\s+(?P<name>.+)")
_SAVE = re.compile(
    r"(?:save|remember|mark)\s+(?:this|the current|current)\s*"
    r"(?:location|spot|place|position|area)\s+as\s+(?:the\s+)?(?P<name>.+)|"
    r"save\s+(?:this\s+)?location\s+(?:as\s+)?(?:the\s+)?(?P<name2>.+)")
_LOOK = re.compile(
    r"look\s+(?P<dir>left|right|up|down|forward|straight|ahead|behind|back|around)")
_SCAN = re.compile(
    r"(?:scan|map)(?:\s+(?:the|this))?(?:\s+(?:room|area|house|place|surroundings|around))?|"
    r"look around(?:\s+(?:the\s+)?(?:room|house|area))?")
_LIST = re.compile(
    r"(?:list|show|tell me|what are)\s+(?:the\s+|your\s+|all\s+)?"
    r"(?:saved\s+)?(?:locations|places|spots)(?:\s+you\s+know)?|"
    r"where can you go")


# ── Vision entry shortcut ────────────────────────────────────────────────────
# NOT a tool lane — this one changes where the GRAPH starts, and it is the only
# thing in this file that does.
#
# "what do you see?" costs THREE LLM calls on the normal path: chat decides to
# hand over, local_agent decides to call look(), then local_agent answers with
# the image. Measured at ~60s end to end on the 12B.
#
# All three calls exist to reach a conclusion we can reach here for free: a
# question about what the robot can see needs the camera frame and the
# multimodal agent. So agent_node grabs the frame itself, staples it to the
# turn exactly the way look() would, and enters at local_agent — which then
# answers in ONE call, from an image already in the conversation.
#
# The image lands in the shared history, so "did he wear spectacles?" still
# works as a follow-up; local_agent is sticky, so that follow-up also skips
# routing. Same guarantee as match(): if this is not CERTAINLY a question
# about the current view, it returns False and the full graph runs.
_VISION_Q = re.compile(
    r"(?:what|who)(?:'?s| is| are| can you| do you| did you)?\s+"
    r"(?:you\s+)?(?:see|seeing|looking at|in front of you|around you|there|"
    r"in the room|in this room)\b.*"
    r"|(?:what|who)(?:'?s| is)\s+(?:in front of|behind|next to|near)\s+you\b.*"
    # _normalize strips a leading "can you "/"do you ", so the pattern has to
    # match what is LEFT: "can you see anything" arrives here as "see anything".
    r"|(?:(?:can|do)\s+you\s+)?see\s+(?:anything|anyone|someone|somebody|"
    r"something|what'?s (?:there|here|around))\b.*"
    r"|(?:is|are)\s+(?:there\s+)?(?:anyone|anybody|someone|somebody|any people)"
    r"\s*(?:here|there|in the room|around|in front of you)?\s*\??"
    r"|describe\s+(?:what\s+you\s+(?:see|can see)|the\s+(?:room|scene|view))\b.*"
    r"|(?:take a look|have a look)\s*(?:at\s+(?:this|that))?"
    r"|what\s+does\s+it\s+look\s+like\b.*"
)

# Phrases the pattern above would otherwise swallow. "look around" is a SCAN
# (drive a full circle); "look left" aims the head. Both are movement, and
# movement must not be answered with a photo.
_NOT_VISION = re.compile(r"look\s+(?:around|left|right|up|down|behind|back)\b")


def is_vision_question(text: str) -> bool:
    """True only when the utterance is CERTAINLY about the robot's current view.

    Used by agent_node to enter the graph at local_agent with the camera frame
    already attached. Anything uncertain returns False and takes the normal
    route — the shortcut never guesses, exactly like match().
    """
    t = _normalize(text)
    if not t or len(t) > 60:
        return False
    if _NOT_VISION.search(t) or _SCAN.fullmatch(t) or _LOOK.fullmatch(t):
        return False
    return bool(_VISION_Q.fullmatch(t))


def match(text: str, known_locations: set | None = None) -> FastIntent | None:
    """Map an utterance to a FastIntent, or None when not CERTAINLY a simple
    movement command (compound sentences, unknown places, paraphrases → LLM)."""
    t = _normalize(text)
    if not t or len(t) > 60:          # long sentences are never simple commands
        return None
    known = {k.lower() for k in (known_locations or set())}

    if _STOP.fullmatch(t):
        return FastIntent("stop")

    m = _TURN.fullmatch(t)
    if m:
        if m.group("dir") == "around":
            return FastIntent("turn", {"dir": "right", "deg": 180.0})
        deg = float(m.group("val")) if m.group("val") else 90.0
        return FastIntent("turn", {"dir": m.group("dir"), "deg": deg})

    m = _MOVE.fullmatch(t)
    if m:
        direction = "B" if m.group("dir").startswith(("back", "rev")) else "F"
        if m.group("val"):
            cm = float(m.group("val"))
            if (m.group("unit") or "").startswith("m") and cm <= 5:
                cm *= 100.0           # "2 meters" (bare big numbers stay cm)
        else:
            cm = 15.0 if m.group("abit") else 30.0
        return FastIntent("move", {"dir": direction, "cm": cm})

    m = _SAVE.fullmatch(t)
    if m:
        name = (m.group("name") or m.group("name2") or "").strip()
        if name:
            return FastIntent("save", {"name": name})

    m = _GOTO.fullmatch(t)
    if m:
        name = m.group("name").strip()
        if name in ("me", "us"):
            return FastIntent("approach", {"target": "person"})
        if _loc_key(name) in known:
            return FastIntent("goto", {"name": name})
        obj = _canon_object(name)
        if obj:
            return FastIntent("approach", {"target": obj})
        return None                    # unknown place — let the LLM handle it

    if _COME.fullmatch(t):
        return FastIntent("approach", {"target": "person"})

    m = _FIND.fullmatch(t)
    if m:
        name = m.group("name").strip()
        if name in ("me", "us"):
            return FastIntent("approach", {"target": "person"})
        obj = _canon_object(name)
        if obj:
            return FastIntent("approach", {"target": obj})
        return None                    # "find my keys" → LLM / memory

    m = _LOOK.fullmatch(t)
    if m:
        if m.group("dir") == "around":
            return FastIntent("scan")
        return FastIntent("look", {"dir": m.group("dir")})

    if _SCAN.fullmatch(t) and any(w in t for w in ("scan", "map", "around")):
        return FastIntent("scan")

    if _LIST.fullmatch(t):
        return FastIntent("list")

    return None


# ── Execution ─────────────────────────────────────────────────────────────────

_VOICE_STATE = {"channel": "voice", "sender_name": "voice", "messages": []}


def try_handle(text: str, say_fn=None, state: dict | None = None) -> str | None:
    """Match + execute. Returns everything that was spoken (for the history
    record) or None when the utterance is not a fast-path command.

    say_fn: reply sink — defaults to the speaker (bridge.publish_speech);
    Telegram turns pass a chat-send closure so nothing reaches the speaker.
    state: turn state handed to the nav tools ({channel, sender_name}) — it
    governs where the deferred arrival report goes. Defaults to voice."""
    bridge = _bridge.get()
    try:
        known = set(bridge.get_known_locations().keys())
    except Exception:
        known = set()
    intent = match(text, known_locations=known)
    if intent is None:
        return None
    logger.info("fastpath: %r → %s %s", text, intent.kind, intent.args)

    sink = say_fn or bridge.publish_speech
    spoken: list[str] = []

    def say(msg: str) -> None:
        spoken.append(msg)
        sink(msg)

    try:
        _execute(intent, bridge, say, state or dict(_VOICE_STATE))
    except Exception as e:
        logger.error("fastpath execution failed: %s", e)
        if not spoken:
            return None                # nothing said yet — let the graph retry
        say("Something went wrong with that move.")
    return " ".join(spoken)


def _execute(intent: FastIntent, bridge, say, state: dict) -> None:
    from .tools.approach import (approach_described_object,
                                list_saved_locations, scan_surroundings)
    from .tools.movement import move_robot, navigate_to_pose, point_camera, save_location

    kind, a = intent.kind, intent.args

    if kind == "stop":
        # _on_user_input already cancelled nav + set the motion interrupt when
        # this utterance arrived; this is the belt-and-braces zero twist.
        bridge.cancel_navigation()
        say("Stopped.")
        move_robot.invoke({"command": "S"})

    elif kind == "move":
        say(f"{'Moving forward' if a['dir'] == 'F' else 'Backing up'} "
            f"{a['cm']:.0f} centimeters.")
        move_robot.invoke({"command": f"{a['dir']}:{a['cm']:.0f}"})

    elif kind == "turn":
        say(f"Turning {'left' if a['dir'] == 'left' else 'right'} {a['deg']:.0f} degrees."
            if a["deg"] != 180.0 else "Turning around.")
        cmd = "L" if a["dir"] == "left" else "R"
        move_robot.invoke({"command": f"{cmd}:{a['deg']:.0f}"})

    elif kind == "goto":
        say(f"On my way to the {a['name']}.")
        result = navigate_to_pose.invoke(
            {"location": _loc_key(a["name"]), "state": dict(state)})
        if "Unknown location" in result:
            say(f"Actually, I don't have '{a['name']}' saved.")

    elif kind == "approach":
        target = a["target"]
        say("Coming to you." if target == "person" else f"Looking for the {target}.")
        # approach_object (YOLO detections + world model) was removed — nothing
        # on this rover ever published /vision/detections_3d. The described
        # -object path does the same job through the VLM, and is the only one
        # that works. It is slower (a VLM round-trip per look), which is why
        # the ack above is spoken BEFORE the call rather than after it.
        result = approach_described_object.invoke(
            {"description": target, "state": dict(state)})
        say(result)

    elif kind == "save":
        say(save_location.invoke({"name": a["name"]}))

    elif kind == "look":
        d = a["dir"]
        if d in ("behind", "back"):
            say("Turning around.")
            move_robot.invoke({"command": "R:180"})
        elif d == "around":
            say("Taking a look around.")
            say(scan_surroundings.invoke({}))
        else:
            # Sign follows point_camera's documented convention: pan is
            # -90 = full LEFT .. +90 = full right. This mapping used to be
            # inverted (left → +60), so one of the two was wrong whichever way
            # the servo is finally mounted. Confirm the physical direction once
            # the mount exists and fix it in ONE place — here and the tool
            # docstring must agree.
            pan = {"left": -60.0, "right": 60.0}.get(d, 0.0)
            tilt = {"up": 25.0, "down": -25.0}.get(d, 0.0)
            # Speak the tool's own answer: with no mount fitted it explains
            # that, where a canned "Looking left." was simply untrue.
            say(point_camera.invoke({"pan_deg": pan, "tilt_deg": tilt}))

    elif kind == "scan":
        say("Scanning the area — doing a full turn.")
        say(scan_surroundings.invoke({}))

    elif kind == "list":
        say(list_saved_locations.invoke({}))
