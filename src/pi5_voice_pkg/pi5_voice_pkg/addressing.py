"""Is this transcript addressed to the robot, and what did they actually ask?

Name-gating is the only thing standing between a room microphone and a robot
that answers the television. But people do not say a robot's name in every
sentence of a conversation — they say it once and then keep talking:

    "rakhi, set a timer"        → addressed
    "for how long?"             ← the robot asks
    "five minutes"              → still talking to it, no name

So the name opens the door and a follow-up window holds it open; this module
handles the first half. Pure — no rclpy, no audio.
"""

import re


def strip_alias(text: str, aliases) -> str | None:
    """Remove the wake name if present.

    Returns the rest of the sentence, `''` when the text was only the name
    (people do say a bare "Rakhi?" to get attention), or None when no name
    appears at all.

    Matching is word-bounded on purpose: a plain substring search fires on an
    alias buried inside an unrelated word, and case must not matter because
    recognisers capitalise names inconsistently.
    """
    if not text:
        return None
    for alias in aliases or ():
        alias = (alias or "").strip()
        if not alias:
            continue
        m = re.search(rf"\b{re.escape(alias)}\b", text, flags=re.IGNORECASE)
        if m:
            rest = text[:m.start()] + text[m.end():]
            # Cutting a name out of the middle leaves a double space behind.
            return re.sub(r"\s+", " ", rest).strip(" ,.!?;:-")
    return None
