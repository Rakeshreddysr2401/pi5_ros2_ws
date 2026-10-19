"""utils/clock_stamp.py: the time stamped on each user turn."""
from datetime import datetime

from langrobo_core.utils.clock_stamp import spoken_time, turn_clock


def test_turn_clock():
    assert turn_clock(datetime(2026, 10, 4, 5, 31)) == \
        "[Time now: 5:31 AM, say: twenty-nine minutes to six in the morning]"
    assert turn_clock(datetime(2026, 10, 4, 17, 5)) == \
        "[Time now: 5:05 PM, say: five minutes past five in the evening]"
    assert turn_clock(datetime(2026, 10, 4, 0, 0)) == "[Time now: 12:00 AM, say: midnight]"


def test_spoken_time():
    t = lambda h, m: spoken_time(datetime(2026, 10, 11, h, m))   # noqa: E731
    assert t(19, 51) == "nine minutes to eight in the evening"   # said "eight past seven" live
    assert t(18, 30) == "half past six in the evening"
    assert t(9, 15) == "quarter past nine in the morning"
    assert t(13, 45) == "quarter to two in the afternoon"
    assert t(8, 0) == "eight o'clock in the morning"
    assert t(12, 0) == "noon"
    assert t(10, 1) == "one minute past ten in the morning"
    assert t(10, 59) == "one minute to eleven in the morning"
    assert t(11, 50) == "ten minutes to twelve -- nearly noon"
    assert t(23, 40) == "twenty minutes to twelve -- nearly midnight"
    assert t(0, 20) == "twenty minutes past twelve at night"
    assert t(16, 40) == "twenty minutes to five in the evening"


def test_prompt_points_at_the_stamp():
    from langrobo_core import prompts, registry
    assert "[Time now:" in prompts.CHAT_PROMPT
    assert "[Time now:" in registry._today_line()
