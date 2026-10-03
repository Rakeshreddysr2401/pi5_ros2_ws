#!/usr/bin/env python3
"""twin_chat.py — talk to the twin brain from a terminal (twin_brain.sh say).

    twin_brain.sh say "go near the chair" [--wait 240]
    twin_brain.sh listen [seconds]

Publishes the text on /voice/user_input -- exactly what stt_node does for the
real robot -- and prints every /voice/robot_speech line the brain says back,
with the time since the request. The ROS domain comes from the environment
(twin_brain.sh sets 42 and refuses 0).

When to stop: a drive returns at once ("on my way") and the arrival report
comes minutes later as a new turn, so it does not stop at the first reply. It
stops after --wait seconds, or once the brain has been idle (not thinking)
and silent for --quiet seconds AFTER the last thing it said.
"""
import argparse
import os
import sys
import time

if os.environ.get("ROS_DOMAIN_ID", "0") == "0":     # before anything can join domain 0
    sys.exit("refusing: ROS_DOMAIN_ID is 0, the REAL robot's (run me through twin_brain.sh)")

import rclpy  # noqa: E402
from rclpy.node import Node  # noqa: E402
from std_msgs.msg import Bool, String  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="*")
    ap.add_argument("--wait", type=float, default=240.0, help="give up after this many seconds")
    ap.add_argument("--quiet", type=float, default=25.0,
                    help="stop once idle and silent this long after the last reply")
    ap.add_argument("--listen", type=float, default=None, help="only print speech, this long")
    a = ap.parse_args()

    rclpy.init()
    node = Node("twin_chat")
    t0 = time.monotonic()
    state = {"thinking": False, "last": None, "said": 0}

    def on_speech(m):
        state["last"] = time.monotonic()
        state["said"] += 1
        print(f"[{time.monotonic() - t0:6.1f}s] MITRA: {m.data}", flush=True)

    def on_thinking(m):
        state["thinking"] = bool(m.data)
        if m.data:
            state["last"] = time.monotonic()

    node.create_subscription(String, "/voice/robot_speech", on_speech, 20)
    node.create_subscription(Bool, "/brain/thinking", on_thinking, 10)

    limit = a.listen if a.listen is not None else a.wait
    if a.listen is None:
        text = " ".join(a.text).strip()
        pub = node.create_publisher(String, "/voice/user_input", 10)
        deadline = time.monotonic() + 10
        while pub.get_subscription_count() == 0 and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.2)
        if pub.get_subscription_count() == 0:
            sys.exit("no twin brain is listening on /voice/user_input (twin_brain.sh up)")
        time.sleep(0.5)                      # let /robot_speech match before the reply
        pub.publish(String(data=text))
        print(f"[   0.0s] YOU:   {text}", flush=True)
        state["last"] = time.monotonic()

    try:
        while time.monotonic() - t0 < limit:
            rclpy.spin_once(node, timeout_sec=0.2)
            if (a.listen is None and state["said"] and not state["thinking"]
                    and time.monotonic() - state["last"] > a.quiet):
                break
    except KeyboardInterrupt:
        pass
    if a.listen is None and not state["said"]:
        print(f"(no reply in {limit:.0f} s -- twin_brain.sh logs)")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
