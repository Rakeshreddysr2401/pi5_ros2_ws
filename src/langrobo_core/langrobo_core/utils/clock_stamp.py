"""The clock on every user turn. Pure zone.

2026-10-04: asked "what is the time now?" twice, the robot answered the second
time from its own history ("five oh eight") 20 minutes late -- the clock was a
tool, and a model with the answer already in the conversation does not call
it. Every time question also cost a second LLM round trip for the tool.

The time rides on the user's message, at the TAIL of the message list like
utils.pose_stamp's body state -- never in the system prompt, where a per-minute
change re-prefilled ~2k tokens on every tick (registry._today_line).
"""

from __future__ import annotations

from datetime import datetime


def turn_clock(now: datetime | None = None) -> str:
    """"[Time now: 5:31 AM]" -- the stamp on a user turn."""
    now = now or datetime.now()
    return f"[Time now: {now.strftime('%I:%M %p').lstrip('0')}]"
