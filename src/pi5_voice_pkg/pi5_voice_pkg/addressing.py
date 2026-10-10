"""Is this transcript addressed to the robot, and what did they actually ask?

Name-gating is the only thing standing between a room microphone and a robot
that answers the television. But people do not say a robot's name in every
sentence of a conversation — they say it once and then keep talking:

    "mitra, set a timer"        → addressed
    "for how long?"             ← the robot asks
    "five minutes"              → still talking to it, no name

So the name opens the door and a follow-up window holds it open; this module
handles the first half. Pure — no rclpy, no audio.
"""

import re


def strip_alias(text: str, aliases) -> str | None:
    """Remove the wake name if present.

    Returns the rest of the sentence, `''` when the text was only the name
    (people do say a bare "Mitra?" to get attention), or None when no name
    appears at all.

    Matching is word-bounded on purpose: a plain substring search fires on an
    alias buried inside an unrelated word, and case must not matter because
    recognisers capitalise names inconsistently.
    """
    if not text:
        return None
    # Longest first: with "mitra" and "hey mitra" both configured, matching
    # the short one first leaves a stray "hey" behind.
    for alias in sorted(((a or "").strip() for a in aliases or ()), key=len, reverse=True):
        if not alias:
            continue
        m = re.search(rf"\b{re.escape(alias)}\b", text, flags=re.IGNORECASE)
        if m:
            # Whole sentences BEFORE the name are not addressed to the robot:
            # one stretch of audio held the room's talk and then the command
            # ("Small water cool. Pizza is ready. I know you like to play.
            # Mitra, follow me" -- 2026-10-10; chat answered all of it). The
            # name starts the request; earlier sentences go. A name inside a
            # sentence keeps it ("Go to the kitchen, Mitra").
            before = text[:m.start()]
            cut = max(before.rfind(c) for c in ".!?")
            if cut >= 0:
                before = before[cut + 1:]
            rest = before + text[m.end():]
            # Cutting a name out of the middle leaves a double space behind.
            return re.sub(r"\s+", " ", rest).strip(" ,.!?;:-")
    return None


# Said before a name, not part of it: "hey friend", "oh friend", "my friend".
_LEAD = r"(?:(?:hey|hi|hello|oh|o|my|dear)[\s,]+)*"
# ...and the ones that make the next word a name being CALLED.
_VOCATIVE = re.compile(r"\b(?:hey|hi|hello|oh|o|dear)\b", re.IGNORECASE)
_PAUSE = re.compile(r"\s*(?:[,.!?:;-]|$)")


def strip_leading_alias(text: str, aliases) -> str | None:
    """Like strip_alias, but for names a translator turns into an ordinary
    word: Sarvam renders "మిత్ర, సినిమాకి వెళ్దామా" as "Friend, shall we go
    to a movie?" -- mitra means friend.

    It counts only at the START of the sentence, and only when it is called:
    followed by a pause ("Friend, ...", "Friend?") or after a calling word
    ("Hey friend play a song"). "My friend is coming over" is a statement and
    stays unaddressed.
    """
    if not text:
        return None
    for alias in sorted(((a or "").strip() for a in aliases or ()), key=len, reverse=True):
        if not alias:
            continue
        m = re.match(rf"\s*(?P<lead>{_LEAD}){re.escape(alias)}\b", text, flags=re.IGNORECASE)
        if not m:
            continue
        rest = text[m.end():]
        if _VOCATIVE.search(m.group("lead")) or _PAUSE.match(rest):
            return rest.strip(" ,.!?;:-")
    return None
