#!/usr/bin/env python3
"""Measure several topics' rates at once: `topic_rates.py [-d SECONDS] TOPIC...`

Prints one line per topic: `<topic> <Hz>` (0.0 when nothing arrived, or the
topic does not exist). Used by `fleet.sh check`.

Why not `ros2 topic hz`: on this Pi 5 it under-reports -- it measured
/wheel_state at 10 Hz while a plain subscriber counted 20.0 (2026-09-27) --
and one CLI process per topic cost about a minute. This is one process, one
DDS discovery, and it counts messages rather than timing gaps.

Subscribes best-effort, which matches both reliable and best-effort
publishers, so sensor topics and ordinary ones are counted alike.
"""
import argparse
import time

import rclpy
from rclpy.qos import qos_profile_sensor_data
from rosidl_runtime_py.utilities import get_message


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-d", "--duration", type=float, default=5.0)
    ap.add_argument("topics", nargs="+")
    args = ap.parse_args()

    rclpy.init()
    node = rclpy.create_node("topic_rates_probe")
    counts = {t: 0 for t in args.topics}

    # Discovery takes a moment; wait until every topic is known, or give up.
    deadline = time.time() + 4.0
    known = {}
    while time.time() < deadline and len(known) < len(args.topics):
        known = {n: ts for n, ts in node.get_topic_names_and_types() if n in counts}
        rclpy.spin_once(node, timeout_sec=0.2)

    for topic, types in known.items():
        def cb(_msg, t=topic):
            counts[t] += 1
        node.create_subscription(get_message(types[0]), topic, cb, qos_profile_sensor_data)

    t0 = time.time()
    while time.time() - t0 < args.duration:
        rclpy.spin_once(node, timeout_sec=0.05)
    elapsed = time.time() - t0

    for topic in args.topics:
        print(f"{topic} {counts[topic] / elapsed:.1f}")
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
