"""says_stop() — while the robot is moving, only a stop word halts it.

Every utterance used to halt the wheels before the graph ran (agent_node
._on_user_input) -- the rule that keeps "stop" from ever depending on the
model. While the robot is MOVING it ended drives on any sound: on 2026-10-10
the first real follow was cancelled 3 s in by the transcript "Friend.", and a
"go near the dining table" search was killed by "Akulam." People talk while a
robot drives. So while it moves -- a background drive (reach, follow, come to
me) or a movement tool still running in the turn (search turns, exact moves)
-- an utterance halts it only if it says stop; anything else is answered
after, with the motion still on (keeps_moving()).

This is a safety gate, not intent routing (CLAUDE.md: no regex intent
matching): it never picks an agent or a tool, and while the robot is still it
is not consulted at all. It errs toward stopping: a stop word anywhere counts
unless negated just before it ("don't stop"); urgent words count ("careful",
"watch out", "wrong", "not there"); a bare "no" / "no no" counts. A new drive
the brain then starts replaces the old one (start_nav_to_pose / start_follow
cancel first; follow_node yields to any goal: "taken over"), and MANUAL on the
phone stops everything as always.

English, Hindi and Telugu (the household's languages; the transcript may come
back in either script).
"""
import re

STOP_WORDS = frozenset({
    # English
    "stop", "halt", "freeze", "wait", "stay", "enough", "pause", "cancel", "quit",
    "careful", "wrong",
    # Hindi (romanised and Devanagari)
    "ruko", "ruk", "rukjao", "bas", "रुको", "रुक", "रुकिए", "बस",
    # Telugu (romanised and Telugu script)
    "aagu", "aagandi", "aapu", "aapandi", "chalu", "chaalu",
    "ఆగు", "ఆగండి", "ఆపు", "ఆపండి", "చాలు",
})

# whole phrases that mean stop without a stop word of their own
STOP_PHRASES = ("don't follow", "do not follow", "dont follow", "no more following",
                "leave me", "go back", "watch out", "look out", "not there", "not that")

# just before a stop word, these turn it into something else ("don't stop")
NEGATIONS = frozenset({"don't", "dont", "not", "never", "without"})

# an utterance made only of these is a "no!" -- urgent, not chatter
_BARE_NO = frozenset({"no", "nope", "hey", "mitra", "nahi", "nahin", "vaddu", "వద్దు", "नहीं"})

# Split on spaces and punctuation, not on \\w: Telugu vowel signs are not \\w,
# and a \\w split cuts "ఆగండి" into pieces that match nothing.
_SPLIT = re.compile(r"[\s,.!?;:\"()\[\]]+")


def says_stop(text: str) -> bool:
    """True if the utterance asks the robot to stop (see module doc)."""
    low = (text or "").lower().replace("’", "'")
    if any(p in low for p in STOP_PHRASES):
        return True
    words = [w.strip("'") for w in _SPLIT.split(low) if w.strip("'")]
    if words and set(words) <= _BARE_NO and set(words) & (_BARE_NO - {"hey", "mitra"}):
        return True
    for i, w in enumerate(words):
        if w in STOP_WORDS and not (NEGATIONS & set(words[max(0, i - 2):i])):
            return True
    return False


def keeps_moving(moving: bool, text: str) -> bool:
    """agent_node's gate: True = leave the motion on and answer the words
    after; False = halt the wheels and abandon the turn in flight, as always."""
    return moving and not says_stop(text)
