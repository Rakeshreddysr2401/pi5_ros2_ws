"""memory_lookup: by meaning, not by words -- word hits need no model; the language
model decides among a shortlist; colour clashes and 'none' are respected."""
import numpy as np
import pytest

from langrobo_core.services import object_memory as om
from langrobo_core.tools import memory_lookup as ml

EPOCH = 5.0


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_OBJECT_MEMORY", str(tmp_path / "om.json"))
    om.set_home(None)
    ml._cache.clear()
    # a stand-in sentence model: words sharing a "family" are alike
    fam = {"tv": 0, "television": 0, "table": 1, "dining": 1, "chair": 2, "bed": 3, "pillow": 3, "bottle": 4}
    def embed(texts):
        out = []
        for t in texts:
            v = np.zeros(6)
            for w in t.split():
                v[fam.get(w, 5)] += 1.0
            out.append(v)
        return out
    monkeypatch.setattr(ml.embedder, "embed", embed)
    yield


def remember(*names):
    for i, n in enumerate(names):
        om.remember(n, float(i), 0.0, None, EPOCH)


def test_a_word_match_needs_no_model(monkeypatch):
    remember("white chair")
    monkeypatch.setattr(ml, "_ask_llm", lambda p: pytest.fail("no model call for a word hit"))
    assert ml.find("the chair", EPOCH)["description"] == "white chair"


def test_tv_finds_television_through_the_language_model(monkeypatch):
    remember("black television", "wooden dining table")
    asked = {}
    def llm(prompt):
        asked["p"] = prompt
        return "1"
    monkeypatch.setattr(ml, "_ask_llm", llm)
    hit = ml.find("tv", EPOCH)
    assert hit["description"] == "black television" and hit["matched_by"] == "meaning"
    assert "1. black television" in asked["p"] and "wooden dining table" not in asked["p"], "shortlist only"


def test_the_language_model_saying_none_is_respected(monkeypatch):
    remember("dining chair")
    monkeypatch.setattr(ml, "_ask_llm", lambda p: "0")
    assert ml.find("dining table", EPOCH) is None


def test_a_colour_clash_is_never_offered(monkeypatch):
    remember("red bottle")
    monkeypatch.setattr(ml, "_ask_llm", lambda p: pytest.fail("a red bottle is not a blue one"))
    assert ml.find("blue water bottle", EPOCH) is None


def test_no_sentence_model_still_asks_with_the_recent_names(monkeypatch):
    remember("television")
    monkeypatch.setattr(ml.embedder, "embed", lambda t: None)
    monkeypatch.setattr(ml, "_ask_llm", lambda p: "1")
    assert ml.find("tv", EPOCH)["description"] == "television"


def test_a_model_failure_is_no_match_not_a_crash(monkeypatch):
    remember("television")
    def boom(p):
        raise RuntimeError("Mac asleep")
    monkeypatch.setattr(ml, "_ask_llm", boom)
    assert ml.find("tv", EPOCH) is None


def test_an_out_of_range_answer_is_no_match(monkeypatch):
    remember("television")
    monkeypatch.setattr(ml, "_ask_llm", lambda p: "7")
    assert ml.find("tv", EPOCH) is None
