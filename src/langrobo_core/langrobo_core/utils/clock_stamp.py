"""The clock on every user turn. Pure zone.

2026-10-04: asked "what is the time now?" twice, the robot answered the second
time from its own history ("five oh eight") 20 minutes late -- the clock was a
tool, and a model with the answer already in the conversation does not call
it. Every time question also cost a second LLM round trip for the tool.

The time rides on the user's message, at the TAIL of the message list like
utils.pose_stamp's body state -- never in the system prompt, where a per-minute
change re-prefilled ~2k tokens on every tick (registry._today_line).

2026-10-11: stamped "[Time now: 7:51 PM]", the 12B model said "about eight
minutes past seven" -- turning digits into speech (SPEECH_STYLE: "half past
six") is arithmetic it gets wrong. So the stamp carries the spoken words too,
worked out here, and the model only has to copy them.
"""

from __future__ import annotations

from datetime import datetime


_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
          "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()


def _number(n: int) -> str:
    return _WORDS[n] if n <= 20 else f"twenty-{_WORDS[n - 20]}"


def _hour(h24: int) -> str:
    return _WORDS[h24 % 12 or 12]


def spoken_time(now: datetime) -> str:
    """7:51 PM -> "nine minutes to eight in the evening", as a person says it."""
    h, m = now.hour, now.minute
    if m == 0 and h in (0, 12):
        return "midnight" if h == 0 else "noon"
    nxt = (h + 1) % 24
    if m == 0:
        words = f"{_hour(h)} o'clock"
    elif m == 15:
        words = f"quarter past {_hour(h)}"
    elif m == 30:
        words = f"half past {_hour(h)}"
    elif m == 45:
        words = f"quarter to {_hour(nxt)}"
    elif m < 30:
        words = f"{_number(m)} minute{'s' if m > 1 else ''} past {_hour(h)}"
    else:
        words = f"{_number(60 - m)} minute{'s' if m < 59 else ''} to {_hour(nxt)}"
    hh = nxt if m > 30 else h          # the hour the words name sets the part of day
    if m > 30 and nxt == 0:
        return f"{words} -- nearly midnight"
    if m > 30 and nxt == 12:
        return f"{words} -- nearly noon"
    part = ("at night" if hh < 5 or hh >= 21 else "in the morning" if hh < 12
            else "in the afternoon" if hh < 17 else "in the evening")
    return f"{words} {part}"


def turn_clock(now: datetime | None = None) -> str:
    """"[Time now: 5:31 AM, say: twenty-nine minutes to six in the morning]"
    -- the stamp on a user turn."""
    now = now or datetime.now()
    return f"[Time now: {now.strftime('%I:%M %p').lstrip('0')}, say: {spoken_time(now)}]"
