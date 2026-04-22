import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, String
from geometry_msgs.msg import Twist
import time

class ChassisPilot(Node):
    """
    Enhanced Chassis Pilot (Pi 5)
    - Mode 1: Visual Servoing (P-Control based on /target_error)
    - Mode 2: Duration-based Movement (F, B, L, R, S based on /movement_cmd)
    """

    def __init__(self):
        super().__init__('chassis_pilot')

        # --- Parameters (Calibrated from your old project) ---
        self.declare_parameter('kp_yaw', 0.5)
        self.declare_parameter('forward_speed_cm_s', 12.0)
        self.declare_parameter('turn_speed_deg_s', 180.0)
        
        self.kp_yaw = self.get_parameter('kp_yaw').value
        self.speed_fwd = self.get_parameter('forward_speed_cm_s').value / 100.0 # Convert to m/s
        self.speed_turn = self.get_parameter('turn_speed_deg_s').value * (3.14159 / 180.0) # Rad/s

        # --- State ---
        self.current_mode = "IDLE" # IDLE, TRACKING, MANUAL
        self.manual_end_time = 0.0

        # --- Subscriptions ---
        self.error_sub = self.create_subscription(Float32, '/target_error', self._error_cb, 10)
        self.cmd_sub = self.create_subscription(String, '/movement_cmd', self._manual_cb, 10)

        # --- Publishers ---
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel', 10)

        # Main Control Loop (20Hz)
        self.timer = self.create_timer(0.05, self._control_loop)

        self.get_logger().info("🏎️ Enhanced Chassis Pilot Ready (Hybrid Control)")

    def _manual_cb(self, msg):
        """
        Handles commands like 'F:20' (Forward 20cm) or 'L:90' (Left 90 deg)
        """
        try:
            parts = msg.data.upper().split(':')
            cmd = parts[0]
            val = float(parts[1]) if len(parts) > 1 else 0.0

            self.get_logger().info(f"Manual Command: {cmd} with value {val}")

            duration = 0.0
            if cmd in ['F', 'B']:
                duration = val / (self.speed_fwd * 100.0)
            elif cmd in ['L', 'R']:
                duration = val / (self.get_parameter('turn_speed_deg_s').value)

            if cmd == 'S':
                self.current_mode = "IDLE"
            else:
                self.current_mode = "MANUAL"
                self.active_cmd = cmd
                self.manual_end_time = time.time() + duration

        except Exception as e:
            self.get_logger().error(f"Error parsing manual command: {e}")

    def _error_cb(self, msg):
        # Only switch to tracking if we aren't in a manual move
        if self.current_mode != "MANUAL":
            self.current_mode = "TRACKING"
            self.latest_error = msg.data

    def _control_loop(self):
        twist = Twist()

        if self.current_mode == "MANUAL":
            if time.time() < self.manual_end_time:
                if self.active_cmd == 'F': twist.linear.x = self.speed_fwd
                elif self.active_cmd == 'B': twist.linear.x = -self.speed_fwd
                elif self.active_cmd == 'L': twist.angular.z = self.speed_turn
                elif self.active_cmd == 'R': twist.angular.z = -self.speed_turn
            else:
                self.current_mode = "IDLE"

        elif self.current_mode == "TRACKING":
            # Visual Servoing Logic
            twist.angular.z = -1.0 * self.latest_error * self.kp_yaw
            if abs(self.latest_error) < 0.2:
                twist.linear.x = 0.1 # Move forward when centered
            
            # Timeout for tracking
            self.current_mode = "IDLE" # Reset and wait for next /target_error

        self.cmd_pub.publish(twist)

def main(args=None):
    rclpy.init(args=args)
    node = ChassisPilot()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
