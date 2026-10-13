"""What the robot SAYS around a tool call, written by code -- pure zone.

WHY (2026-10-12). "Find the helmet and go near it": the model chose the tool
2.3 s in, the tool looked and started the drive at 10.9 s, and the robot said
its first word at 13.5 s -- after a second LLM call that only rephrased the
tool's result. A person says "okay, looking" and then walks. So agent_node
speaks these lines itself, the moment each happens:

  on_call    as the model calls the tool (before it runs) -- for the slow ones
  on_result  as it returns, when the result is a plain success; the model's
             own rephrasing that follows is then NOT spoken (the stream mutes
             that one run -- speech_stream.SpeechStreamHandler.say). A run
             that calls another tool still runs and its later answer is
             spoken, so "turn left and tell me what you see" keeps working.

None = nothing to say: the model speaks as before. Failures always return
None -- the model explains those better than a template can.
"""

import re

_CMD = {"F": ("Moved forward", "centimetres"), "B": ("Moved back", "centimetres"),
        "L": ("Turned left", "degrees"), "R": ("Turned right", "degrees")}

_SEE = re.compile(r"I can see the (.+?) \S+ about ([\d.]+) m away")


def _thing(description: str) -> str:
    """'the my bag' -> 'bag': the article and possessive dropped, for "the X"."""
    return re.sub(r"^(?:(?:the|a|an|my|your|our|his|her|their)\s+)+", "",
                  (description or "").strip(), flags=re.IGNORECASE)


def on_call(name: str, args: dict) -> str | None:
    if name == "approach_described_object":
        thing = _thing((args or {}).get("description", ""))
        return f"Looking for the {thing}." if thing else None
    return None


def on_result(name: str, content) -> str | None:
    text = content if isinstance(content, str) else ""
    if name == "move_robot" and text.startswith("Movement done: "):
        steps = text[len("Movement done: "):].split(".")[0].split(", ")
        if len(steps) == 1 and ":" in steps[0]:
            cmd, _, val = steps[0].partition(":")
            if cmd in _CMD and val.isdigit():
                verb, unit = _CMD[cmd]
                return f"{verb} {val} {unit}."
        return "Done."
    if name == "approach_described_object" and "On my way" in text:
        m = _SEE.search(text)
        if m:
            return f"I see the {_thing(m.group(1))}, about {m.group(2)} metres away. On my way."
        return "On my way."
    if name == "follow_person":
        if text.startswith("Following you now"):
            return "Following you now. Say stop when you want me to stop."
        if text.startswith("Coming to you now"):
            return "Coming to you now."
    if name == "navigate_to_pose" and text.startswith("Navigation started: heading to '"):
        place = text.split("'")[1]
        return f"On my way to {place}."
    return None
