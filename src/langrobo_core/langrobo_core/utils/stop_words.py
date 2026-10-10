"""says_stop() — while "follow me" runs, only a stop word halts the wheels.

Every utterance halts the wheels before the graph runs (agent_node
._on_user_input) -- the rule that keeps "stop" from ever depending on the
model. During "follow me" that rule ended the follow on any sound: on
2026-10-10 the first real follow was cancelled 3 s in by the transcript
"Friend." People talk while they walk with someone. So while FOLLOWING (not
"come to me", which lasts seconds) an utterance halts the wheels only if it
says stop; anything else goes to the brain with the follow still on.

This is a safety gate, not intent routing (CLAUDE.md: no regex intent
matching): it never picks an agent or a tool, and outside a follow it is not
consulted at all. It errs toward stopping: a stop word anywhere counts, unless
negated just before it ("don't stop"). A new drive the brain starts ends the
follow on the Jetson by itself (follow_node: a goal on /goal_exec or /reach is
"taken over"), and MANUAL on the phone stops everything as always.

English, Hindi and Telugu (the household's languages; the transcript may come
back in either script).
"""
import re

STOP_WORDS = frozenset({
    # English
    "stop", "halt", "freeze", "wait", "stay", "enough", "pause", "cancel", "quit",
    # Hindi (romanised and Devanagari)
    "ruko", "ruk", "rukjao", "bas", "रुको", "रुक", "रुकिए", "बस",
    # Telugu (romanised and Telugu script)
    "aagu", "aagandi", "aapu", "aapandi", "chalu", "chaalu",
    "ఆగు", "ఆగండి", "ఆపు", "ఆపండి", "చాలు",
})

# whole phrases that mean "stop following" without a stop word of their own
STOP_PHRASES = ("don't follow", "do not follow", "dont follow", "no more following",
                "leave me", "go back")

# just before a stop word, these turn it into something else ("don't stop")
NEGATIONS = frozenset({"don't", "dont", "not", "never", "without"})

# Split on spaces and punctuation, not on \\w: Telugu vowel signs are not \\w,
# and a \\w split cuts "ఆగండి" into pieces that match nothing.
_SPLIT = re.compile(r"[\s,.!?;:\"()\[\]]+")


def says_stop(text: str) -> bool:
    """True if the utterance asks the robot to stop (see module doc)."""
    low = (text or "").lower().replace("’", "'")
    if any(p in low for p in STOP_PHRASES):
        return True
    words = [w for w in _SPLIT.split(low) if w]
    for i, w in enumerate(words):
        if w.strip("'") in STOP_WORDS and not (NEGATIONS & set(words[max(0, i - 2):i])):
            return True
    return False
