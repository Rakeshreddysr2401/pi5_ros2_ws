"""The wake log must record every decision, flag a misheard name, and never raise."""

import json
import os

import numpy as np

from pi5_voice_pkg.wake_log import WakeLog, name_words, near_miss

ALIASES = ["hey kino", "kino", "keeno"]


def lines(directory):
    (path,) = [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".jsonl")]
    with open(path) as f:
        return [json.loads(line) for line in f]


def test_the_name_is_the_last_word_of_each_alias_and_the_fused_alias():
    assert name_words(ALIASES) == ["heykino", "keeno", "kino"]


def test_a_fused_hey_kino_is_a_near_miss():
    """Heard live 2026-10-20: 'Hey Kino' came out as one word and was ignored."""
    assert near_miss("Ekina, can you go forward?", name_words(ALIASES))[0] == "ekina"


def test_a_misheard_name_is_a_near_miss():
    for heard in ["Keno", "Chino", "Gino", "Keanu", "Kinu", "Kinno"]:
        assert near_miss(f"{heard}, go to the kitchen", ["kino"])[0] == heard.lower()


def test_the_exact_name_or_plain_talk_is_not():
    assert near_miss("Kino, go to the kitchen", ["kino"]) is None
    for talk in ["pizza is ready", "okay", "kind of", "the king", "my skin", "kinda"]:
        assert near_miss(talk, ["kino"]) is None, talk


def test_every_decision_is_one_line(tmp_path):
    log = WakeLog(str(tmp_path), ALIASES)
    pcm = np.zeros(16000, dtype=np.float32)
    log.record("Kino, what time is it?", "wake", pcm=pcm, alias=True, provider="sherpa", latency_ms=400)
    log.record("Okay.", "ignored", pcm=pcm)
    got = lines(tmp_path)
    assert [e["decision"] for e in got] == ["wake", "ignored"]
    assert got[0]["name_heard"] is True and got[0]["audio_s"] == 1.0
    assert got[1]["near"] is None


def test_clips_are_kept_for_wakes_and_near_misses_only(tmp_path):
    log = WakeLog(str(tmp_path), ALIASES)
    pcm = np.full(1600, 0.1, dtype=np.float32)
    log.record("Kino stop", "wake", pcm=pcm, alias=True)
    log.record("Keno come here", "ignored", pcm=pcm)
    log.record("pizza is ready", "ignored", pcm=pcm)
    got = lines(tmp_path)
    assert got[0].get("clip") and got[1].get("clip") and "clip" not in got[2]
    assert len(os.listdir(tmp_path / "clips")) == 2


def test_old_clips_are_pruned(tmp_path):
    log = WakeLog(str(tmp_path), ALIASES, max_clips=2)
    for i in range(4):
        log.record(f"kino {i}", "wake", pcm=np.zeros(160, dtype=np.float32), alias=True)
    assert len(os.listdir(tmp_path / "clips")) <= 2


def test_off_when_no_directory(tmp_path):
    log = WakeLog("", ALIASES)
    assert not log.enabled
    log.record("kino", "wake", alias=True)        # a no-op, not an error


def test_a_broken_directory_disables_it_without_raising(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    log = WakeLog(str(blocker / "wake"), ALIASES)
    log.record("kino", "wake", pcm=np.zeros(10, dtype=np.float32), alias=True)
    assert not log.enabled
