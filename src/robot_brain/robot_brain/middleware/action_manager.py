from rclpy.action import ActionClient

class ActionManager:

    def __init__(self, node):
        self.node = node
        self.actions = {}

    def register(self, name, action_type):
        self.actions[name] = ActionClient(self.node, action_type, name)

    async def send_goal(self, name, goal_msg):
        client = self.actions[name]

        while not client.wait_for_server(timeout_sec=1.0):
            self.node.get_logger().info(f"Waiting for action {name}")

        goal_handle = await client.send_goal_async(goal_msg)
        result = await goal_handle.get_result_async()
        return result