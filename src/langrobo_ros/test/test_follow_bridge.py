"""ROS2Bridge.start_follow / _follow_worker, with no ROS graph.

    cd src/langrobo_ros && python3 -m pytest test/ -q     (needs rclpy installed)

The methods are run on a stand-in `self` carrying only what they touch, so
the real code is tested without a node: the honest first answer (nobody /
unavailable / no answer), the background drive that ends through the
nav-done callback, cancel on a new utterance, and silence from the Jetson.
"""
import json
import threading
import time
import types

import pytest

from langrobo_ros.ros2_bridge import ROS2Bridge


class Pub:
    def __init__(self, subs=1):
        self.subs, self.sent = subs, []

    def get_subscription_count(self):
        return self.subs

    def publish(self, m):
        self.sent.append(m)


def fake(subs=1):
    s = types.SimpleNamespace()
    s._follow_start_pub, s._follow_cancel_pub = Pub(subs), Pub()
    s._status_lock, s._nav_lock = threading.Lock(), threading.Lock()
    s._follow_status = {}
    s._nav_cancel_event = s._nav_thread = s._nav_goal = None
    s._reach_current_key = None
    s._Empty = lambda: "EMPTY"
    s.done = []
    s._fire_nav_done = lambda ok, msg: s.done.append((ok, msg))
    s.cancel_navigation = lambda: s._nav_cancel_event and s._nav_cancel_event.set()
    n = [0]

    def stamp():
        n[0] += 1
        return None, f"k{n[0]}"
    s._stamp_now = stamp
    s.FOLLOW_START_WAIT_S, s.FOLLOW_SILENT_S = 1.0, 10.0
    s._FOLLOW_ENDS = ROS2Bridge._FOLLOW_ENDS
    s._follow_worker = types.MethodType(ROS2Bridge._follow_worker, s)
    return s


def answer_later(s, key, *lines, delay=0.15):
    def go():
        time.sleep(delay)
        for d in lines:
            with s._status_lock:
                s._follow_status.setdefault(key, []).append(dict(d, req=key))
            time.sleep(0.05)
    threading.Thread(target=go, daemon=True).start()


def start(s, mode="follow"):
    return ROS2Bridge.start_follow(s, mode)


def wait_done(s, timeout=3.0):
    t = time.time()
    while not s.done and time.time() - t < timeout:
        time.sleep(0.05)
    return s.done


def test_no_follow_node_is_unavailable_and_sends_nothing():
    s = fake(subs=0)
    r = start(s)
    assert r["result"] == "unavailable" and s._follow_start_pub.sent == []


def test_request_carries_req_and_come_flag():
    s = fake()
    answer_later(s, "k1", {"state": "done", "result": "nobody", "why": "x"})
    start(s, "come")
    sent = json.loads(s._follow_start_pub.sent[0].data)
    assert sent == {"req": "k1", "stop_at_gap": True}


def test_nobody_is_answered_now_with_no_background_drive():
    s = fake()
    answer_later(s, "k1", {"state": "starting"},
                 {"state": "done", "result": "nobody", "why": "no one in view to follow"})
    r = start(s)
    assert r == {"ok": False, "result": "nobody", "why": "no one in view to follow"}
    assert s._nav_thread is None and s.done == []


def test_no_answer_cancels_and_says_unavailable():
    s = fake()
    r = start(s)
    assert r["result"] == "unavailable" and s._follow_cancel_pub.sent == ["EMPTY"]


def test_following_then_lost_reports_through_nav_done():
    s = fake()
    answer_later(s, "k1", {"state": "following", "target": 4, "dist": 1.7})
    r = start(s)
    assert r["ok"] and r["target"] == 4 and s._nav_goal["label"] == "following you"
    answer_later(s, "k1", {"state": "done", "result": "lost", "why": "lost sight of them for 3 s"},
                 delay=0.1)
    (ok, msg), = wait_done(s)
    assert not ok and "lost sight" in msg


def test_come_reached_is_a_success():
    s = fake()
    answer_later(s, "k1", {"state": "following"})
    start(s, "come")
    answer_later(s, "k1", {"state": "done", "result": "reached", "why": "1.02 m from them"},
                 delay=0.1)
    (ok, msg), = wait_done(s)
    assert ok and "come to you" in msg


def test_new_utterance_cancels_on_the_jetson():
    s = fake()
    answer_later(s, "k1", {"state": "following"})
    start(s)
    s.cancel_navigation()                       # what agent_node does on every utterance
    (ok, msg), = wait_done(s)
    assert not ok and "cancelled" in msg and s._follow_cancel_pub.sent == ["EMPTY"]


def test_silence_ends_it():
    s = fake()
    s.FOLLOW_SILENT_S = 0.5
    answer_later(s, "k1", {"state": "following"})
    start(s)
    (ok, msg), = wait_done(s)
    assert not ok and "went silent" in msg and s._follow_cancel_pub.sent == ["EMPTY"]


def test_a_newer_follow_supersedes_a_running_one():
    s = fake()
    answer_later(s, "k1", {"state": "following"})
    start(s)
    answer_later(s, "k2", {"state": "following"})
    start(s)                                     # cancel_navigation inside: the first ends
    (ok, msg), = wait_done(s)
    assert "cancelled" in msg and s._nav_thread.is_alive()
    time.sleep(0.4)
    # ...but it must NOT send /follow/cancel: that would land after the new
    # start and kill the new follow
    assert s._follow_cancel_pub.sent == []


def test_a_reach_drive_after_a_follow_still_stops_the_follow():
    s = fake()
    answer_later(s, "k1", {"state": "following"})
    start(s)
    s.cancel_navigation()          # start_nav_to_pose does this; the follow key stays current
    wait_done(s)
    assert s._follow_cancel_pub.sent == ["EMPTY"]
