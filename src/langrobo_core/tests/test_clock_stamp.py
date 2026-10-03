"""utils/clock_stamp.py: the time stamped on each user turn."""
from datetime import datetime

from langrobo_core.utils.clock_stamp import turn_clock


def test_turn_clock():
    assert turn_clock(datetime(2026, 10, 4, 5, 31)) == "[Time now: 5:31 AM]"
    assert turn_clock(datetime(2026, 10, 4, 17, 5)) == "[Time now: 5:05 PM]"
    assert turn_clock(datetime(2026, 10, 4, 0, 0)) == "[Time now: 12:00 AM]"


def test_prompt_points_at_the_stamp():
    from langrobo_core import prompts, registry
    assert "[Time now:" in prompts.CHAT_PROMPT
    assert "[Time now:" in registry._today_line()
