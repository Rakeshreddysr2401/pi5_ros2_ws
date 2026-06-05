import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String
from geometry_msgs.msg import Twist


class ChassisPilot(Node):
    """
    Translates /movement_cmd String commands → /cmd_vel Twist for ESP32.

    Command format  (published by LangGraph move_robot tool):
        F:<cm>   forward N centimetres
        B:<cm>   backward N centimetres
        L:<deg>  rotate left N degrees
        R:<deg>  rotate right N degrees
        S        stop immediately

    /ir_obstacle (Bool) from ESP32 hard-stops all motion.

    /cmd_vel Twist is also accepted directly so Nav2 can drive the robot
    in the future without any changes here — chassis_pilot just republishes
    whatever Twist it receives on that topic.
    """

    def __init__(self):
        super().__init__('chassis_pilot')

        self.declare_parameter('forward_speed_cms',  12.0)   # cm/s
        self.declare_parameter('turn_speed_degs',   120.0)   # deg/s
        self.declare_parameter('linear_vel_ms',       0.12)  # m/s sent in Twist
        self.declare_parameter('angular_vel_rads',    1.2)   # rad/s sent in Twist

        self._fwd_cms  = self.get_parameter('forward_speed_cms').value
        self._turn_dgs = self.get_parameter('turn_speed_degs').value
        self._lin_vel  = self.get_parameter('linear_vel_ms').value
        self._ang_vel  = self.get_parameter('angular_vel_rads').value

        self._obstacle     = False
        self._mode         = 'IDLE'     # IDLE | TIMED
        self._timed_twist  = Twist()
        self._timed_end_t  = 0.0

        # /movement_cmd: LangGraph agent string commands
        self.create_subscription(String, '/movement_cmd', self._on_cmd,      10)
        # /ir_obstacle: ESP32 IR sensor emergency stop
        self.create_subscription(Bool,   '/ir_obstacle',  self._on_obstacle, 10)

        self._pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.create_timer(0.05, self._loop)   # 20 Hz

        self.get_logger().info('Chassis Pilot ready — publishing /cmd_vel Twist')

    # ── Callbacks ────────────────────────────────────────────────────────────

    def _on_cmd(self, msg: String):
        raw   = msg.data.strip().upper()
        parts = raw.split(':')
        cmd   = parts[0]
        val   = float(parts[1]) if len(parts) > 1 else 0.0

        if cmd == 'S':
            self._mode = 'IDLE'
            self._pub.publish(Twist())   # immediate zero
            return

        twist = Twist()
        duration = 0.0

        if cmd == 'F':
            twist.linear.x  =  self._lin_vel
            duration = val / self._fwd_cms
        elif cmd == 'B':
            twist.linear.x  = -self._lin_vel
            duration = val / self._fwd_cms
        elif cmd == 'L':
            twist.angular.z =  self._ang_vel
            duration = val / self._turn_dgs
        elif cmd == 'R':
            twist.angular.z = -self._ang_vel
            duration = val / self._turn_dgs
        else:
            return

        self._timed_twist = twist
        self._timed_end_t = time.time() + duration
        self._mode        = 'TIMED'

    def _on_obstacle(self, msg: Bool):
        self._obstacle = msg.data
        if self._obstacle:
            self._mode = 'IDLE'
            self._pub.publish(Twist())   # immediate zero

    # ── 20 Hz control loop ────────────────────────────────────────────────────

    def _loop(self):
        if self._obstacle:
            return   # already published zero in callback

        if self._mode == 'TIMED':
            if time.time() < self._timed_end_t:
                self._pub.publish(self._timed_twist)
            else:
                self._mode = 'IDLE'
                self._pub.publish(Twist())   # stop after timed move
        # IDLE: publish nothing — ESP32 watchdog handles it


def main(args=None):
    rclpy.init(args=args)
    node = ChassisPilot()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
