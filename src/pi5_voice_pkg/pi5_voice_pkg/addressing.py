"""Is this transcript addressed to the robot, and what did they actually ask?

Name-gating is the only thing standing between a room microphone and a robot
that answers the television. But people do not say a robot's name in every
sentence of a conversation — they say it once and then keep talking:

    "kino, set a timer"        → addressed
    "for how long?"             ← the robot asks
    "five minutes"              → still talking to it, no name

So the name opens the door and a follow-up window holds it open; this module
handles the first half. Pure — no rclpy, no audio.
"""

import re


def strip_alias(text: str, aliases) -> str | None:
    """Remove the wake name if present.

    Returns the rest of the sentence, `''` when the text was only the name
    (people do say a bare "Kino?" to get attention), or None when no name
    appears at all.

    Matching is word-bounded on purpose: a plain substring search fires on an
    alias buried inside an unrelated word, and case must not matter because
    recognisers capitalise names inconsistently.
    """
    if not text:
        return None
    # Longest first: with "kino" and "hey kino" both configured, matching
    # the short one first leaves a stray "hey" behind.
    for alias in sorted(((a or "").strip() for a in aliases or ()), key=len, reverse=True):
        if not alias:
            continue
        m = re.search(rf"\b{re.escape(alias)}\b", text, flags=re.IGNORECASE)
        if m:
            # Whole sentences BEFORE the name are not addressed to the robot:
            # one stretch of audio held the room's talk and then the command
            # ("Small water cool. Pizza is ready. I know you like to play.
            # Kino, follow me" -- 2026-10-10; chat answered all of it). The
            # name starts the request; earlier sentences go. A name inside a
            # sentence keeps it ("Go to the kitchen, Kino").
            before = text[:m.start()]
            cut = max(before.rfind(c) for c in ".!?")
            if cut >= 0:
                before = before[cut + 1:]
            # "Hey" heard as "Play" (or Pay/They/Eh) right before the name is a
            # call, not a request: "Play Kino, go back" must reach the brain as
            # "go back", not "play go back" (Apple, 2026-10-20: the mic drops the
            # breathy H, the vowel starts like a P, and "Play..." is the likeliest
            # first word to say to an assistant).
            if _CALL_ONLY.fullmatch(before):
                before = ""
            rest = before + text[m.end():]
            # Cutting a name out of the middle leaves a double space behind.
            return re.sub(r"\s+", " ", rest).strip(" ,.!?;:-")
    return None


# Said before a name, not part of it: "hey friend", "oh friend", "my friend".
_LEAD = r"(?:(?:hey|hi|hello|oh|o|my|dear)[\s,]+)*"
# ...and the ones that make the next word a name being CALLED.
_VOCATIVE = re.compile(r"\b(?:hey|hi|hello|oh|o|dear)\b", re.IGNORECASE)
# Words a recogniser writes for a "Hey" it did not hear: only ever dropped when
# they come straight before the name.
_CALL_ONLY = re.compile(r"\s*(?:(?:hey|hi|hello|oh|o|play|pay|they|eh|a)[\s,]+)+", re.IGNORECASE)
_PAUSE = re.compile(r"\s*(?:[,.!?:;-]|$)")


def strip_leading_alias(text: str, aliases) -> str | None:
    """Like strip_alias, but for names a translator turns into an ordinary
    word: a name that means something in the speaker's language comes back
    translated (a name meaning "friend": "Friend, shall we go to a movie?").

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


def sounds(word: str) -> str:
    """A rough sound key: recognisers write 'kino' as keno, chino, gino, keanu.

    Hard c/ch/g/q -> k, front vowels -> i, back vowels -> o, a final 'a' -> o,
    doubles collapsed. Plain letter similarity got this backwards (2026-10-20):
    'kind', 'king' and 'skin' scored as close as 'keno', 'chino' fell short.
    """
    w = (word or "").lower()
    for a, b in (("ch", "k"), ("ck", "k"), ("c", "k"), ("q", "k"), ("g", "k"),
                 ("ee", "i"), ("ea", "i"), ("ie", "i"), ("e", "i"), ("y", "i"),
                 ("oo", "o"), ("ou", "o"), ("u", "o"), ("h", "")):
        w = w.replace(a, b)
    w = re.sub(r"a$", "o", w)          # "kina": an unstressed last vowel drifts
    return re.sub(r"(.)\1+", r"\1", w)


def sounds_like(word: str, names) -> bool:
    """The word is the name as a recogniser misspelt it.

    Same sound key, or with a vowel glued on the front ("Ekina", "Akina" --
    "Hey Kino" said fast) or up to two consonants on the end ("Kinoff").
    Heard live on Parakeet, 2026-10-20 (logs/wake).
    """
    key = sounds(word)
    return any(key == k or re.fullmatch(rf"[aio]?{re.escape(k)}[^aeiou]{{0,2}}", key)
               for k in (sounds(n) for n in names or () if n))


def strip_sound_alike(text: str, names) -> str | None:
    """Like strip_leading_alias, for the name as a recogniser misspelt it.

    A sound-alike is weaker evidence than the name, so it counts only where
    a name is CALLED: first word of a sentence followed by a pause ("Ekina,
    please move back.") or after a calling word ("Hey Kinoff what's the
    time"). Sentences before it are dropped, as in strip_alias. "I bought
    chinos" or "Gino is coming" stays unaddressed.
    """
    if not text:
        return None
    starts = [0] + [m.end() for m in re.finditer(r"[.!?]\s*", text)]
    for start in starts:
        m = re.match(rf"\s*(?P<lead>{_LEAD})(?P<word>[^\W\d_]+)", text[start:], flags=re.IGNORECASE)
        if not m or not sounds_like(m.group("word"), names):
            continue
        rest = text[start + m.end():]
        if _VOCATIVE.search(m.group("lead")) or _PAUSE.match(rest):
            return rest.strip(" ,.!?;:-")
    return None
