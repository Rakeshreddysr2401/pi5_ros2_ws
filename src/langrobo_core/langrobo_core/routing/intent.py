"""Entry classifier — which agent a user turn ENTERS, picked on the Pi in ~15 ms.

INTENT_ROUTING_PLAN.md is the design; this is its step 1-3. Today a move or a
vision question enters chat, and chat spends a whole LLM call deciding to hand
over: median 3.0 s over 64 real handovers (journal, 2026-09-23..10-06), up to
18 s cold. A confident pick here enters the right agent directly and that call
never happens.

It picks the ENTRY AGENT only. It does not pick tools or read numbers ("forward
30" is navigate's job to parse), and it never guesses: below the threshold, or
too close to a second agent, or nearest to an ABSTAIN example, the answer is
None and today's rule runs (the sticky agent, else chat). A wrong confident pick
costs one handover -- what every such turn costs today -- so the thresholds are
tuned for precision, not coverage.

How: nearest-neighbour cosine between the utterance and each agent's examples
(registry.py `examples` + `intent_examples`), on a small sentence embedder.
The embedder is injected (`embed(list[str]) -> array`), so the tests run with
a fake and no model. Measured 2026-10-07 on the 188 distinct real utterances
that entered chat (scratch eval, bge-small-en-v1.5): decided 75, 74 right
(99 %), skipping 43 of the 67 handovers those turns needed.

ABSTAIN: short replies ("yes", "ok", "haha", "what?") belong to whoever asked
the question -- "yes" to navigate's "shall I go to the kitchen?" must stay with
navigate (sticky). Their nearest example wins nothing.

Pure: no ROS, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

# Replies that only mean something to the agent that asked. Nearest to one of
# these -> no pick, the sticky agent (or chat) keeps the turn.
ABSTAIN_EXAMPLES = (
    "yes", "no", "ok", "okay", "cool", "haha", "what?", "sorry", "fine",
    "do it", "go ahead", "that one", "the second one", "really?", "hmm",
    "yes please", "no thanks", "then we can go", "what did you say",
)

# Navigate moves the wheels: a wrong entry there acts on chatter, so it needs
# more certainty than a wrong entry anywhere else.
DEFAULT_THRESHOLDS = {"navigate": 0.80}
DEFAULT_THRESHOLD = 0.80
DEFAULT_MARGIN = 0.06

# Calling the robot is not part of the request: "Mitra, what do you see" and
# "what do you see" must embed alike. "friend" is how Sarvam translates the
# name (mitra = friend, pi5_voice_pkg addressing.py).
_ADDRESS = re.compile(
    r"^\s*((hey|hi|hello|ok|okay|oh)\s+)?(mitra|mithra|friend|jarvis)\b[\s,.!?:-]*",
    re.IGNORECASE)


def strip_address(text: str) -> str:
    return _ADDRESS.sub("", text or "").strip()


@dataclass(frozen=True)
class Pick:
    agent: Optional[str]      # the agent to enter, or None (no confident pick)
    score: float              # best cosine
    runner_up: float          # best cosine of any OTHER bucket
    nearest: str              # the bucket that scored best (may be "abstain")

    @property
    def margin(self) -> float:
        return self.score - self.runner_up


class IntentRouter:
    def __init__(self, examples: dict[str, list[str]],
                 embed: Callable[[list[str]], np.ndarray],
                 thresholds: dict[str, float] | None = None,
                 threshold: float = DEFAULT_THRESHOLD,
                 margin: float = DEFAULT_MARGIN,
                 abstain: tuple[str, ...] = ABSTAIN_EXAMPLES):
        self._embed = embed
        self._thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._threshold = threshold
        self._margin = margin
        labels, texts = [], []
        for agent, xs in examples.items():
            for x in xs:
                labels.append(agent)
                texts.append(strip_address(x))
        for x in abstain:
            labels.append("abstain")
            texts.append(x)
        self._labels = np.array(labels)
        self._bank = _unit(np.asarray(embed(texts), dtype=np.float32))
        self._buckets = sorted(set(labels))

    def classify(self, text: str) -> Pick:
        q = strip_address(text)
        if not q:
            return Pick(None, 0.0, 0.0, "abstain")
        v = _unit(np.asarray(self._embed([q]), dtype=np.float32))[0]
        sims = self._bank @ v
        best = {b: float(sims[self._labels == b].max()) for b in self._buckets}
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        (top, score), (_, second) = ranked[0], ranked[1]
        need = self._thresholds.get(top, self._threshold)
        ok = top != "abstain" and score >= need and score - second >= self._margin
        return Pick(top if ok else None, score, second, top)


def entry_for(pick: Pick | None, default_entry: str) -> tuple[str, bool]:
    """(the agent to enter, whether the classifier decided it). Same contract
    as services/jev.entry_for: a confident pick wins, anything else is
    today's rule."""
    if pick is not None and pick.agent and pick.agent != default_entry:
        return pick.agent, True
    return default_entry, False


def _unit(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(n == 0, 1, n)
