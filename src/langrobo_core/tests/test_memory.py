"""Facts memory (services/memory.py + tools/memory.py): what people TELL the robot.

A fake embedder (word overlap as cosine) stands in for bge-small. What must hold:
  * a fact survives a restart (it is a file, not process state);
  * recall finds by meaning, best first, and says who said it;
  * an exact repeat refreshes, a similar fact is never overwritten;
  * forget removes only a clear single match;
  * guests are refused (CAP_MEMORY), voice acts as the owner;
  * no embedder -> word-overlap search still works (rule 5).
"""

import re
import sys

import numpy as np
import pytest

from langrobo_core.services import embedder
from langrobo_core.services import memory as store
from langrobo_core.tools.memory import memory

_VOCAB: dict[str, int] = {}
_SKIP = {"a", "the", "is", "are", "her", "his", "at", "in", "to", "does", "what", "where"}


class FakeModel:
    def embed(self, texts):
        for t in texts:
            v = np.zeros(2048, dtype=np.float32)
            for w in re.findall(r"[a-z]+", t.lower()):
                if w not in _SKIP:
                    v[_VOCAB.setdefault(w, len(_VOCAB))] += 1.0
            yield v


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGROBO_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(store, "RECALL_FLOOR", 0.3)
    monkeypatch.setattr(store, "FORGET_FLOOR", 0.5)
    embedder._reset_for_tests(model=FakeModel())
    yield
    embedder._reset_for_tests()


def _call(action, text, role="owner", name="Rakesh"):
    return memory.invoke({"action": action, "text": text,
                          "state": {"sender_role": role, "sender_name": name}})


def test_remember_then_recall_by_meaning():
    _call("remember", "Amma takes her BP pills at 9 pm")
    _call("remember", "Rakesh likes filter coffee")
    out = _call("recall", "when does Amma take pills?")
    assert out.splitlines()[0].startswith("- Amma takes her BP pills at 9 pm")
    assert "told by Rakesh, today" in out


def test_survives_a_restart(tmp_path):
    store.remember("the spare keys are in the blue drawer", who="Amma")
    embedder._reset_for_tests(model=FakeModel())        # a new process, same file
    assert (tmp_path / "memory.db").exists()
    hits = store.recall("where are the spare keys")
    assert hits and hits[0]["text"] == "the spare keys are in the blue drawer"


def test_exact_repeat_refreshes_and_similar_is_kept_apart():
    assert store.remember("Amma likes tea")[0] == "added"
    assert store.remember("amma likes tea.")[0] == "refreshed"
    assert store.remember("Dad likes tea")[0] == "added"
    assert store.count() == 2


def test_forget_needs_one_clear_match():
    store.remember("Amma likes tea")
    store.remember("Dad likes tea")
    assert store.forget("likes tea") is None               # which one?
    assert "Not sure which one" in _call("forget", "likes tea")
    assert store.forget("Dad likes tea")["text"] == "Dad likes tea"
    assert [h["text"] for h in store.recall("tea")] == ["Amma likes tea"]


def test_guest_is_refused_and_voice_is_owner():
    assert "Permission denied" in _call("remember", "the safe code is 1234", role="guest")
    assert store.count() == 0
    out = memory.invoke({"action": "remember", "text": "Rakesh's birthday is 24 January",
                         "state": {}})
    assert out.startswith("Remembered")


def test_nothing_found_says_so():
    assert _call("recall", "what is Dad's car") == "Nothing remembered about that."
    assert _call("forget", "anything") == "Nothing like that is remembered."


def test_without_an_embedder_word_search_still_works(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastembed", None)
    embedder._reset_for_tests()
    store.remember("Amma takes her BP pills at 9 pm")
    hits = store.recall("Amma pills")
    assert hits and "BP pills" in hits[0]["text"]
    assert store.recall("weather tomorrow") == []
