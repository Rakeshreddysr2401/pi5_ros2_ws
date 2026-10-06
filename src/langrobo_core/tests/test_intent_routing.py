"""Entry classifier (langrobo_core/routing): which agent a user turn enters.

No model: a bag-of-words fake stands in for the sentence embedder, so cosine
is word overlap. What must hold:
  * a clear request enters its agent; anything unsure is None (never a guess);
  * "yes" / "ok" stay with whoever asked (ABSTAIN), never a pick;
  * navigate needs more certainty than the others;
  * the robot's name ("Mitra", Sarvam's "Friend") is not part of the request;
  * off / not loaded / no fastembed -> None, and routing is today's;
  * intent_examples never reach a prompt (KV caches untouched).
"""

import re
import sys

import numpy as np
import pytest

from langrobo_core import routing
from langrobo_core.registry import AGENTS, SPECS
from langrobo_core.routing.intent import (IntentRouter, Pick, entry_for,
                                          strip_address)

_VOCAB: dict[str, int] = {}


def fake_embed(texts):
    """Bag of words over a growing vocabulary: cosine = word overlap."""
    rows = []
    for t in texts:
        for w in re.findall(r"[a-z']+", t.lower()):
            _VOCAB.setdefault(w, len(_VOCAB))
        rows.append(re.findall(r"[a-z']+", t.lower()))
    m = np.zeros((len(texts), 4096), dtype=np.float32)
    for i, words in enumerate(rows):
        for w in words:
            m[i, _VOCAB[w]] += 1.0
    return m


EXAMPLES = {
    "navigate": ["go to the kitchen", "turn left ninety degrees", "move forward one metre"],
    "local_agent": ["what do you see", "is anyone on the sofa"],
    "chat": ["tell me a joke", "what is the weather today"],
}


@pytest.fixture
def router():
    return IntentRouter(EXAMPLES, fake_embed, threshold=0.8, margin=0.06)


def test_clear_requests_enter_their_agent(router):
    assert router.classify("go to the kitchen").agent == "navigate"
    assert router.classify("what do you see").agent == "local_agent"
    assert router.classify("tell me a joke").agent == "chat"


def test_unsure_is_no_pick(router):
    p = router.classify("my cousin arrives on friday")
    assert p.agent is None


def test_short_replies_abstain(router):
    for reply in ("yes", "ok", "haha", "go ahead"):
        p = router.classify(reply)
        assert p.agent is None, reply
        assert p.nearest == "abstain"


def test_robot_name_is_not_part_of_the_request(router):
    assert strip_address("Mitra, what do you see?") == "what do you see?"
    assert strip_address("Hey Friend go to the kitchen") == "go to the kitchen"
    assert strip_address("my friend is here") == "my friend is here"
    assert router.classify("Mitra, go to the kitchen").agent == "navigate"


def test_navigate_needs_more_certainty():
    r = IntentRouter(EXAMPLES, fake_embed, thresholds={"navigate": 0.99},
                     threshold=0.5, margin=0.0)
    p = r.classify("go to the kitchen now")      # close, not exact
    assert p.nearest == "navigate" and p.agent is None
    assert r.classify("tell me a joke please").agent == "chat"


def test_margin_refuses_a_tie():
    r = IntentRouter({"navigate": ["the red bag"], "local_agent": ["the red bag"]},
                     fake_embed, threshold=0.1, margin=0.06, abstain=())
    assert r.classify("the red bag").agent is None


def test_entry_for_only_acts_on_a_pick_that_changes_the_entry():
    pick = Pick("navigate", 0.9, 0.5, "navigate")
    assert entry_for(pick, "chat") == ("navigate", True)
    assert entry_for(pick, "navigate") == ("navigate", False)
    assert entry_for(Pick(None, 0.9, 0.88, "chat"), "local_agent") == ("local_agent", False)
    assert entry_for(None, "chat") == ("chat", False)


# ── the service wrapper ─────────────────────────────────────────────────────

@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(routing, "_router", None)
    monkeypatch.setitem(routing._state, "error", None)
    monkeypatch.setitem(routing._state, "loading", False)
    for k in routing.stats:
        monkeypatch.setitem(routing.stats, k, 0)
    return routing


def test_off_by_default(svc, monkeypatch):
    monkeypatch.delenv("LANGROBO_INTENT_ROUTING", raising=False)
    monkeypatch.setattr(svc, "_router", IntentRouter(EXAMPLES, fake_embed))
    assert svc.mode() == "off"
    assert svc.classify("go to the kitchen") is None


def test_not_loaded_routes_as_before(svc, monkeypatch):
    monkeypatch.setenv("LANGROBO_INTENT_ROUTING", "on")
    assert svc.classify("go to the kitchen") is None


def test_missing_fastembed_degrades(svc, monkeypatch):
    monkeypatch.setenv("LANGROBO_INTENT_ROUTING", "on")
    from langrobo_core.services import embedder
    monkeypatch.setitem(sys.modules, "fastembed", None)    # import raises
    embedder._reset_for_tests()
    svc._load()
    embedder._reset_for_tests()
    assert not svc.ready()
    assert "fastembed" in svc.status()["error"]
    assert svc.classify("go to the kitchen") is None


def test_shadow_classifies_and_scores_outcomes(svc, monkeypatch):
    monkeypatch.setenv("LANGROBO_INTENT_ROUTING", "shadow")
    monkeypatch.setattr(svc, "_router", IntentRouter(EXAMPLES, fake_embed))
    pick = svc.classify("go to the kitchen")
    assert pick.agent == "navigate"
    fields = svc.record_outcome(pick, "navigate", acted=False)
    assert fields["actual"] == "navigate"
    s = svc.status()
    assert s["turns"] == 1 and s["picked_precision"] == 1.0 and s["acted"] == 0


def test_every_agent_has_examples_and_none_reach_a_prompt():
    ex = routing.examples()
    assert set(ex) == set(SPECS)
    assert all(len(v) >= 10 for v in ex.values())
    for meta in AGENTS.values():                  # the routing copy chat renders
        assert set(meta) == {"description", "examples"}
