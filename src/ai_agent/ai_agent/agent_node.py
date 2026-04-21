ros2 pkg create robot_brain --build-type ament_python --dependencies rclpy std_msgsimport rclpy
from rclpy.node import Node
from std_msgs.msg import String

class DummyAgent(Node):

    def __init__(self):
        super().__init__('dummy_agent')

        self.subscription = self.create_subscription(
            String,
            'voice_text',
            self.callback,
            10
        )

        self.publisher = self.create_publisher(
            String,
            'agent_response',
            10
        )

        self.get_logger().info("Dummy Agent Ready ✅")

    def callback(self, msg):
        text = msg.data
        self.get_logger().info(f"Received: {text}")

        reply = "Hi from Pi 5!"

        response = String()
        response.data = reply
        self.publisher.publish(response)

def main(args=None):
    rclpy.init(args=args)
    node = DummyAgent()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
