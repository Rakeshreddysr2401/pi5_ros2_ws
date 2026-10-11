#!/usr/bin/env python3
"""twin_chat.py — talk to the twin brain from a terminal (twin_brain.sh say).

    twin_brain.sh say "go near the chair" [--wait 240]
    twin_brain.sh listen [seconds]

Publishes the text on /voice/user_input -- exactly what stt_node does for the
real robot -- and prints every /voice/robot_speech line the brain says back,
with the time since the request. The ROS domain comes from the environment
(twin_brain.sh sets 42 and refuses 0). `--real` talks to the REAL robot on
domain 0 instead (scripts/say.sh): `ros2 topic pub --once` loses about half its
turns there -- it sends before the brain has matched it (2026-10-11).

When to stop: a drive returns at once ("on my way") and the arrival report
comes minutes later as a new turn, so it does not stop at the first reply. It
stops after --wait seconds, or once the brain has been idle (not thinking)
and silent for --quiet seconds AFTER the last thing it said.
"""
import argparse
import os
import sys
import time

if os.environ.get("ROS_DOMAIN_ID", "0") == "0" and "--real" not in sys.argv:   # before joining domain 0
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
    ap.add_argument("--real", action="store_true", help="allow domain 0: the REAL robot (say.sh)")
    a = ap.parse_args()

    rclpy.init()
    node = Node("say" if a.real else "twin_chat")
    t0 = time.monotonic()
    state = {"thinking": False, "last": None, "said": 0}

    def on_speech(m):
        state["last"] = time.monotonic()
        text = m.data.replace("<|eou|>", "").strip()   # end-of-utterance marker, for the speaker
        if not text:
            return
        state["said"] += 1
        print(f"[{time.monotonic() - t0:6.1f}s] MITRA: {text}", flush=True)

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
        # Wait for the BRAIN by name: pi5_media listens on this topic too, and
        # "anyone matched" let a turn go out before agent_node had (2026-10-11).
        def brain_listening():
            return any(i.node_name == "agent_node"
                       for i in node.get_subscriptions_info_by_topic("/voice/user_input"))
        deadline = time.monotonic() + 20
        while not brain_listening() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.2)
        if not brain_listening():
            sys.exit("no brain is listening on /voice/user_input"
                     + ("" if a.real else " (twin_brain.sh up)"))
        t_match = time.monotonic() + 1.5     # the brain must match US too, and /robot_speech
        while time.monotonic() < t_match:
            rclpy.spin_once(node, timeout_sec=0.1)
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
        print(f"(no reply in {limit:.0f} s -- " + ("journalctl -u langrobo-brain" if a.real else "twin_brain.sh logs") + ")")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
