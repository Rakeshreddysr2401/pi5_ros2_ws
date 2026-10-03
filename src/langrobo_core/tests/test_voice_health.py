"""services/voice_health.py: a degraded voice is announced, once, in words."""
from langrobo_core.services import voice_health as vh

QUOTA = 'sarvam translate HTTP 402: {"error":{"message":"No credits available."}}'


def test_why():
    assert vh.why(QUOTA) == "the Sarvam account has no credits left"
    assert vh.why("HTTP 401 invalid api key") == "the Sarvam key was refused"
    assert vh.why("Read timed out") == "the internet or the Sarvam service can't be reached"
    assert vh.why("") == "the Sarvam service isn't responding"


def test_once_per_window_and_not_without_a_fallback():
    n = vh.FallbackNotices(every_s=3600)
    assert n.check("tts", {"fell_back": False}, 0) is None
    first = n.check("tts", {"fell_back": True, "fallback_reason": QUOTA}, 10)
    assert first and "no credits left" in first and "English" in first
    assert n.check("tts", {"fell_back": True, "fallback_reason": QUOTA}, 20) is None
    assert n.check("tts", {"fell_back": True, "fallback_reason": QUOTA}, 3700) is not None


def test_one_heads_up_covers_both_legs():
    n = vh.FallbackNotices(every_s=3600)
    assert n.check("stt", {"fell_back": True, "fallback_reason": QUOTA}, 0)
    assert n.check("tts", {"fell_back": True, "fallback_reason": QUOTA}, 5) is None   # same outage
    assert n.check("tts", {"fell_back": True, "fallback_reason": QUOTA}, 30) is None  # and remembered
