"""relevance.py (pure parts) and the 'heard' chime."""
import json

import numpy as np
import pytest

from pi5_voice_pkg import relevance as R


def test_request_body_carries_context_and_grammar():
    b = R.request_body("Five minutes.", "For how long should I set the timer?")
    user = b["messages"][1]["content"]
    assert "For how long" in user and "Five minutes." in user
    assert b["grammar"] == 'root ::= "yes" | "no"' and b["max_tokens"] == 2 and b["temperature"] == 0
    assert "Robot's last words" not in R.request_body("hello")["messages"][1]["content"]


@pytest.mark.parametrize("content,want", [("yes", True), (" Yes\n", True), ("no", False), ("", False)])
def test_parse(content, want):
    assert R.parse({"choices": [{"message": {"content": content}}]}) is want


def test_parse_garbage_is_no():
    assert R.parse({}) is False and R.parse(None) is False


def test_unreachable_is_none():
    assert R.is_for_robot("hi", "", "http://127.0.0.1:9/v1/chat/completions", timeout=0.5) is None


def test_is_for_robot_with_a_fake_server(monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "yes"}}]}).encode()

    monkeypatch.setattr(R.urllib.request, "urlopen", lambda req, timeout=0: Resp())
    assert R.is_for_robot("What is the time?", "", "http://x/v1/chat/completions") is True


def test_heard_chime():
    pytest.importorskip("rclpy")
    from pi5_voice_pkg.tts_node import heard_chime
    c = heard_chime(24000)
    assert 0.15 < len(c) / 24000 < 0.25
    assert c.dtype == np.float32 and np.max(np.abs(c)) <= 0.26
    assert abs(c[0]) < 0.05 and abs(c[-1]) < 0.05          # faded: no click


@pytest.mark.parametrize("text", ["Louder.", "louder please", "Next song", "next", "Skip it",
                                  "Pause it.", "Resume", "volume up", "Turn it down", "a bit quieter",
                                  "Stop the music."])
def test_music_controls(text):
    assert R.music_control(text)


@pytest.mark.parametrize("text", ["What is next on my list?", "Turn left", "Play Kesariya",
                                  "He said louder voices win", "Stop talking to him", ""])
def test_not_bare_music_controls(text):
    assert not R.music_control(text)


def test_context_goes_first():
    user = R.request_body("Next song.", "", "[music playing: Kesariya]")["messages"][1]["content"]
    assert user.startswith("[music playing: Kesariya]")
