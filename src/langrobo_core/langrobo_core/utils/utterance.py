"""Endpointing helpers — deciding when a spoken utterance is actually finished.

The VAD ends an utterance after ~600ms of silence, but a person pausing to
think ("go to the… umm… kitchen") outlasts that routinely, so one sentence
arrives as two. The brain then answered the fragment and discarded the rest.

`looks_incomplete` is the cheap half of the fix: hold an utterance that ends on
a dangling word for one merge window, and concatenate whatever follows. It is
deliberately conservative — a false "complete" costs nothing (current
behaviour), while a false "incomplete" delays every turn, so the tail list only
contains words that cannot end an English sentence.

Pure zone: no rclpy, no ROS types.
"""

import re

# Words that leave a sentence hanging. A transcript ending here is almost
# certainly mid-thought rather than finished.
CONTINUATION_TAILS = frozenset({
    # conjunctions / subordinators
    "and", "or", "but", "so", "because", "if", "when", "while", "that", "then",
    # prepositions / particles
    "to", "of", "for", "with", "at", "in", "on", "from", "into", "about",
    # determiners
    "the", "a", "an", "my", "your", "our", "this", "these", "some",
    # pronouns + auxiliaries that need a verb after them
    "i", "we", "you", "he", "she", "they", "it",
    "is", "are", "was", "were", "can", "could", "would", "should", "will",
    "do", "does", "did", "have", "has", "had",
    # very common dangling verbs
    "want", "need", "like", "go", "get", "make", "put", "tell", "give", "take",
    # fillers
    "um", "umm", "uh", "uhh", "er", "hmm", "eh",
})

_TERMINAL = ".!?…"
_WORD = re.compile(r"[\w']+")


def looks_incomplete(text: str) -> bool:
    """True if `text` reads as a sentence the speaker had not finished.

    Terminal punctuation wins: both Sarvam and Whisper punctuate their output,
    so a trailing '.' or '?' is a strong finished signal.
    """
    t = (text or "").strip()
    if not t:
        return False
    if t[-1] in _TERMINAL:
        return False
    words = _WORD.findall(t.lower())
    return bool(words) and words[-1] in CONTINUATION_TAILS


def join_utterances(first: str, second: str) -> str:
    """Stitch a held utterance to its continuation as one sentence."""
    return f"{first.rstrip()} {second.lstrip()}".strip()
