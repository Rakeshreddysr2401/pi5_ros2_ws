#!/usr/bin/env python3
"""wheel_odom_relay — ESP32 /wheel_state -> nav_msgs/Odometry /wheel_odom for the EKF.

The ESP32 firmware (rover_firmware_v2.ino) publishes MEASURED wheel linear
velocities as geometry_msgs/Vector3 on /wheel_state (BEST_EFFORT, ~20 Hz):
    x = velL (m/s)   y = velR (m/s)   z = commanded body vx (m/s, debug only)
It can't publish nav_msgs/Odometry itself — that message exceeds the micro-ROS
WiFi/UDP MTU. This node does the differential-drive forward kinematics and
republishes a proper Odometry so robot_localization (Jetson config/ekf.yaml,
odom1) can fuse FORWARD VELOCITY. That gives the fused odom low-latency
dead-reckoning that survives the brief cuVSLAM tracking dropout during fast
pivots (README §10) — the exact failure mode that made rotations twitchy.

Kinematics MUST match the firmware (WHEEL_BASE_M = 0.34 m):
    vx = 0.5 * (velL + velR)
    wz = (velR - velL) / WHEEL_BASE
Only TWIST (vx, wz) is published; pose is left at zero with huge covariance —
the EKF takes absolute x/y/yaw from cuVSLAM, not from wheels (slip). This node
publishes NO TF (the EKF owns odom->base_link). Which twist components actually
get fused is decided by odom1_config in ekf.yaml (vx only by default; the gyro
owns yaw-rate — flip vyaw on there later if wheels prove reliable in pivots).
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from geometry_msgs.msg import Vector3
from nav_msgs.msg import Odometry

WHEEL_BASE_M = 0.34   # MUST match firmware rover_firmware_v2.ino WHEEL_BASE_M


class WheelOdomRelay(Node):
    def __init__(self):
        super().__init__('wheel_odom_relay')
        self.wheel_base = self.declare_parameter('wheel_base', WHEEL_BASE_M).value
        self.odom_frame = self.declare_parameter('odom_frame', 'odom').value
        self.base_frame = self.declare_parameter('base_frame', 'base_link').value
        # Measurement variances for the EKF. Encoders are good on vx, noisier on
        # vyaw (differential of two wheels + slip). (0.05 m/s)^2 and ~(0.14 rad/s)^2.
        self.var_vx = self.declare_parameter('var_vx', 0.0025).value
        self.var_vyaw = self.declare_parameter('var_vyaw', 0.02).value

        # Match the firmware's BEST_EFFORT telemetry publisher, or we get nothing.
        sub_qos = QoSProfile(depth=10,
                             reliability=ReliabilityPolicy.BEST_EFFORT,
                             history=HistoryPolicy.KEEP_LAST)
        self.sub = self.create_subscription(Vector3, '/wheel_state', self.cb, sub_qos)
        self.pub = self.create_publisher(Odometry, '/wheel_odom', 10)
        self.get_logger().info(
            f'wheel_odom_relay up: /wheel_state -> /wheel_odom '
            f'(wheel_base={self.wheel_base} m, {self.odom_frame}->{self.base_frame})')

    def cb(self, m: Vector3):
        vel_l, vel_r = m.x, m.y
        vx = 0.5 * (vel_l + vel_r)
        wz = (vel_r - vel_l) / self.wheel_base

        od = Odometry()
        od.header.stamp = self.get_clock().now().to_msg()
        od.header.frame_id = self.odom_frame
        od.child_frame_id = self.base_frame
        od.twist.twist.linear.x = vx
        od.twist.twist.angular.z = wz

        # 6x6 row-major twist covariance [vx, vy, vz, vroll, vpitch, vyaw].
        tcov = [0.0] * 36
        tcov[0] = self.var_vx      # vx
        tcov[7] = 1e6              # vy   (non-holonomic: not measured)
        tcov[14] = 1e6             # vz
        tcov[21] = 1e6             # vroll
        tcov[28] = 1e6             # vpitch
        tcov[35] = self.var_vyaw   # vyaw
        od.twist.covariance = tcov

        # Pose is NOT measured here -> huge covariance so it can never be fused.
        pcov = [0.0] * 36
        for i in (0, 7, 14, 21, 28, 35):
            pcov[i] = 1e6
        od.pose.covariance = pcov

        self.pub.publish(od)


def main():
    rclpy.init()
    node = WheelOdomRelay()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
