"""memory_lookup -- find something in object memory by what it IS, not its exact words.

"Go near the TV" when memory holds "black television"; "the dining table" when it
holds "wooden table". The word match (object_memory.match_score) needs the same
head word, so those missed and the robot searched for a thing it had seen.

The sentence model on the Pi (services/embedder.py, bge-small) cannot decide this
alone -- measured on it 2026-11-16 over 20 same-thing and 20 different-thing pairs:
"dining table" ~ "dining chair" 0.874, ABOVE "dustbin" ~ "trash can" 0.672 and
"bed" ~ "double bed" 0.789; at 0.80 it found 13/20 and wrongly matched 3/20. So:

    words (object_memory.recall)  ── a hit ──▶ done, no model at all
       └ none ──▶ sentence model: a SHORTLIST of remembered names (>= MIN_COS, at most
                  SHORTLIST; colour clashes out) ──▶ the language model picks the one
                  that is the same kind of thing, or none ──▶ its latest sighting

A remembered spot is a HINT: approach drives to a viewpoint and looks before going
near, and forgets it if it is not there.
"""

import logging
import re

import numpy as np

from ..services import embedder
from ..services import object_memory

logger = logging.getLogger(__name__)

SHORTLIST = 8          # names the language model chooses from
MIN_COS = 0.55         # below every same-thing pair measured (lowest 0.672): a shortlist, not a verdict
NO_MODEL_LIST = 30     # sentence model not loaded: the most recent names go to the judge as they are

_cache: dict = {}


def _vec(texts):
    missing = [t for t in texts if t not in _cache]
    if missing:
        got = embedder.embed(missing)
        if got is None:
            return None
        for t, v in zip(missing, got):
            v = np.asarray(v, float)
            _cache[t] = v / (np.linalg.norm(v) or 1.0)
    return [_cache[t] for t in texts]


def shortlist(query: str, names: list) -> list:
    """The remembered names worth asking about, most alike first."""
    q = object_memory.object_part(query) or query
    names = [n for n in names if not object_memory.colours_clash(query, n)]
    if not names:
        return []
    vecs = _vec([q] + [object_memory.object_part(n) or n for n in names])
    if vecs is None:
        return names[:NO_MODEL_LIST]
    sims = [(float(vecs[0] @ v), n) for v, n in zip(vecs[1:], names)]
    sims = [t for t in sims if t[0] >= MIN_COS]
    sims.sort(key=lambda t: -t[0])
    return [n for _, n in sims[:SHORTLIST]]


_PROMPT = ("A person asked a home robot for: \"{q}\".\n"
           "The robot remembers seeing these things:\n{items}\n"
           "Which ONE of them is the thing asked for -- the same kind of object, any "
           "wording (a TV is a television, a couch is a sofa), but NOT a different "
           "object that is only related (a dining chair is not a dining table, a "
           "pillow is not a bed, a bag is not a box)? If a colour was asked for, it "
           "must agree. Reply with only the number, or 0 if none of them is it.")


def _ask_llm(prompt: str) -> str:
    """The language model's reply (text only, one shot, the vision-tool slot)."""
    from langchain_core.messages import HumanMessage
    from . import survey as _survey
    llm = _survey.vision_tool_llm(max_tokens=8)
    return str(llm.invoke([HumanMessage(content=prompt)],
                          config={"run_name": "memory_lookup", "tags": ["memory_lookup"]}).content)


def judge(query: str, names: list) -> str | None:
    if not names:
        return None
    items = "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1))
    try:
        reply = _ask_llm(_PROMPT.format(q=query, items=items))
    except Exception as e:          # no model: no by-meaning match, the search still works
        logger.warning("memory lookup: language model failed: %s", e)
        return None
    m = re.search(r"\d+", reply or "")
    k = int(m.group(0)) if m else 0
    return names[k - 1] if 1 <= k <= len(names) else None


def find(description: str, epoch, now: float | None = None) -> dict | None:
    """The remembered sighting that answers `description`, or None.
    Word match first (no model); else shortlist + language model."""
    hits = object_memory.recall(description, epoch, now=now)
    if hits:
        return hits[0]
    by_name = object_memory.labels(epoch, now=now)
    if not by_name:
        return None
    pick = judge(description, shortlist(description, list(by_name)))
    if pick is None:
        return None
    logger.info("memory lookup: %r -> %r (by meaning)", description, pick)
    return {**by_name[pick][0], "matched_by": "meaning"}
