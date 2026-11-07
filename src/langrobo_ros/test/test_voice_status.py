"""Voice degradation must be visible without making a paid cloud request."""
from types import SimpleNamespace

from langrobo_ros.agent_node import AgentNode


def test_voice_status_is_unknown_until_observed():
    assert AgentNode._voice_status(SimpleNamespace()) == {"stt": None, "tts": None}


def test_voice_status_shows_configured_and_actual_provider(monkeypatch):
    monkeypatch.setattr("langrobo_ros.agent_node.time.time", lambda: 100)
    node = SimpleNamespace(_tts_meta=(95, {
        "provider": "piper", "configured_provider": "sarvam_stream",
        "fell_back": True, "ok": True, "fallback_reason": "HTTP 402: No credits",
        "text": "not part of status",
    }))
    status = AgentNode._voice_status(node)
    assert status["tts"] == {
        "provider": "piper", "configured_provider": "sarvam_stream",
        "fell_back": True, "ok": True, "fallback_reason": "HTTP 402: No credits", "age_s": 5,
    }
    assert status["stt"] is None
