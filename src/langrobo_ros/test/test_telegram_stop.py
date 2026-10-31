"""A Telegram stop word halts the wheels at once (agent_node._on_telegram_inbound).

    cd src/langrobo_ros && python3 -m pytest test/ -q     (needs rclpy installed)

2026-10-31: "follow me" works from Telegram, so "stop" from the phone must not
wait behind a running turn and an LLM call. Run on a stand-in `self`.
"""
import threading
import types

import pytest

from langrobo_ros.agent_node import AgentNode


def fake(moving):
    s = types.SimpleNamespace()
    s.stops = []
    s._bridge = types.SimpleNamespace(
        navigation_active=lambda: moving,
        cancel_navigation=lambda: s.stops.append("cancel"),
        request_motion_stop=lambda: s.stops.append("motion"))
    s._turn_moving = False
    s._queue_lock = threading.Lock()
    s._telegram_pending = []
    s._input_event = threading.Event()
    s.get_logger = lambda: types.SimpleNamespace(info=lambda *a: None, error=lambda *a: None)
    return s


@pytest.mark.parametrize("text", ["stop", "Stop!", "kino stop", "stop following me"])
def test_stop_while_moving_halts_now_and_still_queues(text):
    s = fake(moving=True)
    AgentNode._on_telegram_inbound(s, types.SimpleNamespace(text=text))
    assert s.stops == ["cancel", "motion"]
    assert len(s._telegram_pending) == 1 and s._input_event.is_set()


def test_other_talk_while_moving_does_not_stop():
    s = fake(moving=True)
    AgentNode._on_telegram_inbound(s, types.SimpleNamespace(text="what time is it"))
    assert s.stops == [] and len(s._telegram_pending) == 1


def test_stop_while_still_only_queues():
    s = fake(moving=False)
    AgentNode._on_telegram_inbound(s, types.SimpleNamespace(text="stop"))
    assert s.stops == [] and len(s._telegram_pending) == 1
