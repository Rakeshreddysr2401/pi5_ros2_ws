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


# ── the filter's own KV slot (2026-10-10: unpinned, it evicted the photo cache) ──

def test_body_pins_only_when_given_a_slot():
    assert "id_slot" not in R.request_body("turn left")
    assert R.request_body("turn left", slot=4)["id_slot"] == 4


def test_the_filter_slot_is_past_the_brains_four():
    # slots 0-3 are the brain's (registry.py): chat, local_agent, navigate, vision tools
    assert R.ADDRESSING_SLOT == 4


def test_props_url():
    assert R.props_url("http://mac.local:8080/v1/chat/completions") == "http://mac.local:8080/props"
    assert R.props_url("http://mac.local:8080/v1/chat/completions/") == "http://mac.local:8080/props"


def test_server_slots_unreachable_is_none():
    assert R.server_slots("http://127.0.0.1:9/v1/chat/completions", timeout=0.5) is None


def test_hold_while_busy():
    h = R.HoldWhileBusy(max_age_s=20)
    assert h.offer("what time is it?", 0.0) is False          # idle: send at once
    h.set_busy(True, 1.0)
    assert h.offer("Do you have an audio?", 2.0) is True      # busy: held
    assert h.offer("How do you mean?", 3.0) is True           # newest wins
    assert h.set_busy(False, 10.0) == "How do you mean?"      # turn over: sent
    assert h.set_busy(False, 11.0) is None                    # sent once only
    h.set_busy(True, 12.0)
    h.offer("old news", 12.0)
    assert h.set_busy(False, 40.0) is None                    # stale: dropped
    h.set_busy(True, 41.0)
    assert h.set_busy(True, 42.0) is None                     # still busy: nothing
    assert h.offer("hello?", 41.0 + 301) is False             # busy for 5 min+: brain died, send
