"""utils/drive_watch.py: say when a drive struggles, stop when it is stuck.

Replays the real drive of 2026-10-29 (Telegram: "go near the black bag"): the
Jetson's reach lines, at their real offsets from the start. reach gave up only
at 305 s, after 7 attempts; the asker heard nothing until then.
"""
import pytest

from langrobo_core.tools.movement import nav_report, report_sentence
from langrobo_core.utils import drive_watch as dw
from langrobo_core.utils.drive_watch import DriveWatch, plain_why

# (seconds since the goal, the status line) -- from the Jetson's /tmp/reach.log
REAL_DRIVE = [
    (0.1, {"phase": "widest", "width_cm": None, "need_cm": 42}),
    (1.2, {"attempt": 1, "phase": "recover", "why": "no path: planner found none (asked before driving)"}),
    (6.4, {"attempt": 1, "phase": "tight", "plan": "F35 R15 F65 L15 F75", "why": ""}),
    (9.4, {"attempt": 1, "phase": "tight", "move": "F35", "outcome": "reached",
           "why": "goal_exec reached: 0.6 cm, +0.3 deg"}),
    (52.2, {"attempt": 1, "phase": "tight", "move": "F55", "outcome": "failed",
            "why": "goal_exec refused: obstacle 0.30 m into the 0.34 m leg"}),
    (90.6, {"attempt": 2, "phase": "goal_exec", "dist_m": 0.3}),
    (104.8, {"attempt": 2, "phase": "recover", "why": "goal_exec refused: something 0.24 m away is "
             "in the -40 deg swing -- after 3 turn-and-shuffle -- nav2 next"}),
    (105.0, {"attempt": 3, "phase": "recover",
             "why": "no path: the widest way is under nav2's 56 cm -- tight route first"}),
    (121.2, {"attempt": 4, "phase": "recover", "why": "no path: planner found none (asked before driving)"}),
    (305.1, {"result": "failed", "why": "gave up after 7 attempts, 305 s"}),
]


def replay(watch, until=400.0, here=(0.75, 0.0)):
    events, lines = [], []
    for t, line in REAL_DRIVE:
        if t > until:
            break
        lines.append(line)
        act = watch.step(list(lines), t, lambda: here)
        if act:
            events.append((t, *act))
    return events


def test_the_real_drive_says_once_then_stops_at_105_s_not_305():
    events = replay(DriveWatch("near the black bag", goal_xy=(1.85, -0.26)))
    kinds = [(round(t), k) for t, k, _ in events]
    assert kinds == [(52, "progress"), (105, "give_up")]


def test_the_progress_line_is_plain():
    (_, _, text), *_ = replay(DriveWatch("near the black bag", goal_xy=(1.85, -0.26)))
    assert text == ("Still trying to get near the black bag: I can't find a way through. "
                    "I'm trying another way.")


def test_the_give_up_report_says_why_how_long_and_where():
    events = replay(DriveWatch("near the black bag", goal_xy=(1.85, -0.26)), here=(0.75, -0.26))
    why = events[-1][2]
    message = f"Navigation to 'near the black bag' failed after retrying -- {why}."
    said = report_sentence(False, message)
    assert said.startswith("I couldn't get near the black bag: the way there is too narrow for me.")
    assert "After 3 tries in 105 s I've stopped, about 1.1 m short of it." in said
    assert said.endswith("ask me again and I'll try once more.")
    # never reported as "cancelled": nav_report would drop it
    assert nav_report(False, message)["cancelled"] is False


def test_the_position_is_only_asked_for_on_giving_up():
    asked = []
    watch = DriveWatch("near the box", goal_xy=(1.0, 0.0))
    for t, line in REAL_DRIVE[:6]:
        watch.step([line], t, lambda: asked.append(t) or (0.0, 0.0))
    assert asked == []


def test_a_drive_that_works_never_reports():
    watch = DriveWatch("near the door", goal_xy=(2.0, 0.0))
    lines = [{"attempt": 1, "phase": "nav2"}]
    for t in range(0, 200, 5):                 # long, but no attempt failed
        assert watch.step(lines, float(t)) is None
    lines.append({"result": "reached"})
    assert watch.step(lines, 201.0) is None


def test_one_quick_retry_says_so_but_does_not_give_up():
    watch = DriveWatch("near the box")
    lines = [{"attempt": 1, "phase": "recover", "why": "no path: planner found none"},
             {"attempt": 2, "phase": "nav2"}]
    assert watch.step(lines, 12.0)[0] == "progress"
    assert watch.step(lines, 300.0) is None    # 2 attempts: still patient
    lines.append({"attempt": 3, "phase": "recover", "why": "no path"})
    assert watch.step(lines, 30.0) is None     # 3 attempts but only 30 s
    assert watch.step(lines, 95.0)[0] == "give_up"
    assert watch.step(lines, 99.0) is None     # once


@pytest.mark.parametrize("why,said", [
    ("no path: planner found none (asked before driving)", "I can't find a way through"),
    ("no path: the widest way is under nav2's 56 cm -- tight route first",
     "the way there is too narrow for me"),
    ("goal_exec refused: something 0.24 m away is in the -40 deg swing", "something is in my way"),
    ("goal_exec stalled", "I got stuck"),
    ("", "I can't get through"),
])
def test_reasons_in_plain_words(why, said):
    assert plain_why(why) == said


def test_limits_come_from_the_environment(monkeypatch):
    monkeypatch.setattr(dw, "GIVE_UP_S", 30.0)
    monkeypatch.setattr(dw, "GIVE_UP_ATTEMPTS", 2)
    watch = DriveWatch("x")
    lines = [{"attempt": 2, "phase": "recover", "why": "no path"}]
    assert watch.step(lines, 31.0)[0] == "give_up"
