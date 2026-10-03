"""Say so when the voice degrades -- don't switch languages silently. Pure zone.

2026-10-04, 04:00: Sarvam answered HTTP 402 "No credits available" and both
directions fell back to English as designed -- and nobody was told. Alexa
would say. stt_node / tts_node now put the reason on their *_meta when they
fall back; agent_node passes each meta here and speaks what comes back.
"""

from __future__ import annotations

NOTICE_EVERY_S = 6 * 3600        # one heads-up per leg per 6 h, not one per sentence


def why(reason: str) -> str:
    """A provider's error text -> what a person should hear."""
    r = (reason or "").lower()
    if "402" in r or "credit" in r or "quota" in r:
        return "the Sarvam account has no credits left"
    if "401" in r or "403" in r or "key" in r or "auth" in r:
        return "the Sarvam key was refused"
    if "timed out" in r or "timeout" in r or "connection" in r or "resolve" in r or "network" in r:
        return "the internet or the Sarvam service can't be reached"
    return "the Sarvam service isn't responding"


class FallbackNotices:
    """Remembers when each leg last warned. leg is "stt" or "tts"."""

    def __init__(self, every_s: float = NOTICE_EVERY_S):
        self.every_s = every_s
        self._last: dict[str, float] = {}

    def check(self, leg: str, meta: dict, now: float) -> str | None:
        """The sentence to speak for this meta, or None (no fallback, or warned recently)."""
        if not meta.get("fell_back"):
            return None
        if now - self._last.get(leg, -1e18) < self.every_s:
            return None
        # one heads-up covers both legs: they fail together on a cloud outage
        if now - max(self._last.values(), default=-1e18) < 60:
            self._last[leg] = now
            return None
        self._last[leg] = now
        reason = why(meta.get("fallback_reason", ""))
        if leg == "stt":
            return (f"Heads up: I can't understand Telugu right now -- {reason} -- "
                    "so please speak English until it's fixed.")
        return (f"Heads up: my Telugu voice isn't working right now -- {reason} -- "
                "so I'll use English until it's fixed.")
