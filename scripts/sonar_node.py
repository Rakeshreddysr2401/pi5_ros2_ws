#!/usr/bin/env python3
"""sonar_node.py -- the two front sonars on ROS: /sonar/left, /sonar/right (sensor_msgs/Range).

The sensors (HC-SR04 type, front corners, 6.5 cm up, 28 cm apart; ~/rover
description/params.yaml `sonar`) are read by their OWN ESP32 running MicroPython
(~/rover/phase1/firmware/sonar/main.py), USB to this Pi 5. It prints a line per
reading, alternating, ~15 Hz each:  "S1 1234" (mm) / "S2 -1" (no echo).

Here: each line -> one Range in frame sonar_left / sonar_right (the rover URDF),
range in metres, +inf for no echo (REP 117). Which of S1/S2 is LEFT is
LEFT_IS_S1 (params.yaml sonar.left_is_s1). /sonar/status (JSON, 1 Hz): rate per
sensor, last reading's age, the port -- what `fleet.sh check` can read.

The board is opened by its USB SOCKET (by-path): it shares the wheels' board's
by-id name (both CP2102 ..._0001), and the wheels' agent owns that one. Opened
with DTR/RTS low so the ESP32 is neither reset nor put in its bootloader. A
lost board (unplugged, rebooting) is re-opened every 2 s; readings stop, the
topics say +inf (no echo) at 10 Hz meanwhile, nothing else is touched -- the wheels
never depend on this.
"""
import json
import math
import os
import time

import rclpy
import serial
from rclpy.node import Node
from sensor_msgs.msg import Range
from std_msgs.msg import String

PORT = os.environ.get('SONAR_DEV', '/dev/serial/by-path/platform-xhci-hcd.0-usb-0:1:1.0-port0')
LEFT_IS_S1 = os.environ.get('SONAR_LEFT_IS_S1', '1') == '1'
FOV, RMIN, RMAX = 0.52, 0.02, 4.0                  # params.yaml sonar.fov / min_range / max_range


class SonarNode(Node):
    def __init__(self):
        super().__init__('sonar')
        self.pub = {'S1': self.create_publisher(Range, '/sonar/left' if LEFT_IS_S1 else '/sonar/right', 10),
                    'S2': self.create_publisher(Range, '/sonar/right' if LEFT_IS_S1 else '/sonar/left', 10)}
        self.frame = {'S1': 'sonar_left' if LEFT_IS_S1 else 'sonar_right',
                      'S2': 'sonar_right' if LEFT_IS_S1 else 'sonar_left'}
        self.p_status = self.create_publisher(String, '/sonar/status', 10)
        self.ser, self.buf, self.next_open = None, b'', 0.0
        self.count = {'S1': 0, 'S2': 0}
        self.last = {'S1': 0.0, 'S2': 0.0}
        self.version = ''
        self.create_timer(0.01, self.poll)
        self.create_timer(1.0, self.status)

    def open(self):
        try:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = PORT, 115200, 0
            s.dtr = False
            s.rts = False
            s.open()
            self.ser, self.buf = s, b''
            self.get_logger().info(f'sonar board open: {PORT}')
        except (serial.SerialException, OSError) as e:
            self.ser = None
            self.next_open = time.time() + 2.0
            self.get_logger().warning(f'sonar board not available ({e}); retrying every 2 s', throttle_duration_sec=30)

    def poll(self):
        if self.ser is None:
            # board missing: say "no echo" for both at ~10 Hz, so a last close reading
            # never lingers in nav2's collision monitor (it does not time sonars out)
            if time.time() - getattr(self, 'last_none', 0.0) >= 0.1:
                self.last_none = time.time()
                for k in ('S1', 'S2'):
                    self.publish(k, math.inf)
            if time.time() >= self.next_open:
                self.open()
            return
        try:
            self.buf += self.ser.read(4096)
        except (serial.SerialException, OSError) as e:
            self.get_logger().warning(f'sonar board lost: {e}')
            self.ser = None
            self.next_open = time.time() + 2.0
            return
        while b'\n' in self.buf:
            line, self.buf = self.buf.split(b'\n', 1)
            self.on_line(line.decode(errors='replace').strip())

    def on_line(self, line):
        if line.startswith('#'):
            self.version = line[1:].strip()
            return
        parts = line.split()
        if len(parts) != 2 or parts[0] not in self.pub:
            return
        try:
            mm = int(parts[1])
        except ValueError:
            return
        self.publish(parts[0], math.inf if mm < 0 else max(RMIN, mm / 1000.0))
        self.count[parts[0]] += 1
        self.last[parts[0]] = time.time()

    def publish(self, key, metres):
        m = Range()
        m.header.stamp = self.get_clock().now().to_msg()
        m.header.frame_id = self.frame[key]
        m.radiation_type = Range.ULTRASOUND
        m.field_of_view, m.min_range, m.max_range = FOV, RMIN, RMAX
        m.range = metres
        self.pub[key].publish(m)

    def status(self):
        now = time.time()
        d = dict(port=PORT, open=self.ser is not None, board=self.version, left_is_s1=LEFT_IS_S1,
                 rate_hz={k: v for k, v in self.count.items()},
                 age_s={k: (round(now - v, 2) if v else None) for k, v in self.last.items()})
        self.count = {'S1': 0, 'S2': 0}
        self.p_status.publish(String(data=json.dumps(d)))


def main():
    rclpy.init()
    n = SonarNode()
    try:
        rclpy.spin(n)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        if n.ser:
            n.ser.close()


if __name__ == '__main__':
    main()
