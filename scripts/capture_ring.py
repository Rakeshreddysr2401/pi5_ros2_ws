#!/usr/bin/env python3
"""Take 8 photos 45 deg apart, turning in place -- a fixed set for vision tests.

    python3 scripts/capture_ring.py --go [OUT_DIR]

Saves view_<i>.jpg + views.json (robot pose and camera stamp per photo) in
OUT_DIR (default test_data/ring_<date>, not in git). Every vision change can
then be measured on the same photos without moving the robot again
(the 2026-10-01 set was lost with /tmp at a power cycle).

Turns through the Jetson's goal_exec, as the brain's bridge.turn_by does:
closed on the measured heading, refused if something is in the swing.

SAFETY: needs --go, and the owner watching the rover. Aborts if teleop is
MANUAL (or unreachable), if a turn is not 'reached', and on Ctrl-C (sends
/goal_exec/cancel).
"""
import datetime
import json
import math
import os
import sys
import time
import urllib.request

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Empty, String
import tf2_ros

STEPS, STEP_DEG, SETTLE_S = 8, 45.0, 2.5


def manual() -> bool:
    try:
        return json.load(urllib.request.urlopen("http://127.0.0.1:8091/mode", timeout=2))["manual"]
    except Exception:
        return True                     # unknown = not allowed to move


class Ring(Node):
    def __init__(self):
        super().__init__("photo_ring_capture")
        self.tf = tf2_ros.Buffer()
        tf2_ros.TransformListener(self.tf, self)
        self.frame, self.frames_seen, self.status = None, 0, {}
        self.create_subscription(CompressedImage, "/camera/color/image_raw/compressed", self._img, 2)
        self.create_subscription(String, "/goal_exec/status", self._st, 20)
        self.turn_pub = self.create_publisher(PoseStamped, "/goal_exec/turn", 10)
        self.cancel_pub = self.create_publisher(Empty, "/goal_exec/cancel", 10)

    def _img(self, m):
        self.frame = (bytes(m.data), m.header.stamp.sec + m.header.stamp.nanosec * 1e-9)
        self.frames_seen += 1

    def _st(self, m):
        try:
            d = json.loads(m.data)
        except ValueError:
            return
        self.status.setdefault(d.get("goal_stamp"), []).append(d)

    def spin_for(self, s):
        end = time.monotonic() + s
        while time.monotonic() < end:
            rclpy.spin_once(self, timeout_sec=0.05)

    def pose(self):
        t = self.tf.lookup_transform("odom", "base_link", rclpy.time.Time())
        q = t.transform.rotation
        yaw = math.degrees(math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y ** 2 + q.z ** 2)))
        return round(t.transform.translation.x, 3), round(t.transform.translation.y, 3), round(yaw, 1)

    def turn(self, deg):
        x, y, yaw = self.pose()
        th = math.radians(yaw + deg)
        st = self.get_clock().now().to_msg()
        key = f"{st.sec}.{st.nanosec:09d}"
        p = PoseStamped()
        p.header.frame_id, p.header.stamp = "odom", st
        p.pose.position.x, p.pose.position.y = float(x), float(y)
        p.pose.orientation.z, p.pose.orientation.w = math.sin(th / 2), math.cos(th / 2)
        self.turn_pub.publish(p)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 40:
            rclpy.spin_once(self, timeout_sec=0.05)
            if manual():
                self.cancel_pub.publish(Empty())
                return {"result": "manual"}
            done = [d for d in self.status.get(key, []) if d.get("state") == "done"]
            if done:
                return done[-1]
        self.cancel_pub.publish(Empty())
        return {"result": "timeout"}

    def photo(self, new=3):
        """The 3rd frame to ARRIVE from now (the Pi and Jetson clocks differ by
        ~1.5 s, so camera stamps are not compared with the Pi's clock)."""
        start, t0 = self.frames_seen, time.monotonic()
        while time.monotonic() - t0 < 10:
            rclpy.spin_once(self, timeout_sec=0.05)
            if self.frames_seen - start >= new:
                return self.frame
        return None


def main():
    if "--go" not in sys.argv:
        sys.exit("refusing: pass --go (the owner must be watching the rover)")
    if manual():
        sys.exit("refusing: teleop is MANUAL (or unreachable)")
    args = [a for a in sys.argv[1:] if a != "--go"]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = args[0] if args else os.path.join(here, "test_data", f"ring_{datetime.date.today()}")
    os.makedirs(out, exist_ok=True)
    rclpy.init()
    n = Ring()
    for _ in range(50):                 # the position arrives over TF: wait for it
        n.spin_for(0.2)
        if n.tf.can_transform("odom", "base_link", rclpy.time.Time()):
            break
    else:
        sys.exit("refusing: no odom -> base_link position after 10 s (is the Jetson's fused layer up?)")
    views = []
    try:
        for i in range(STEPS):
            if i:
                r = n.turn(STEP_DEG)
                print(f"turn {i}: {r.get('result')} {r.get('why', '')}", flush=True)
                if r.get("result") != "reached":
                    print("ABORT: turn not reached", flush=True)
                    break
                n.spin_for(SETTLE_S)
            pose = n.pose()
            f = n.photo()
            if f is None:
                print("ABORT: no camera frame", flush=True)
                break
            path = os.path.join(out, f"view_{i}.jpg")
            with open(path, "wb") as fh:
                fh.write(f[0])
            views.append({"i": i, "file": path, "pose": pose, "stamp": f[1]})
            print(f"photo {i}: pose {pose}", flush=True)
    except KeyboardInterrupt:
        n.cancel_pub.publish(Empty())
        print("ABORT: Ctrl-C, cancel sent", flush=True)
    with open(os.path.join(out, "views.json"), "w") as fh:
        json.dump(views, fh, indent=1)
    print(f"saved {len(views)} photo(s) in {out}", flush=True)
    n.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
