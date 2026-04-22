import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from robot_interfaces.msg import VisionQuery
from typing import TypedDict, Annotated, List, Union
import operator

# --- State Definition ---
class AgentState(TypedDict):
    input: str
    target_object: str
    history: Annotated[List[str], operator.add]
    next_step: str

class LangGraphBrain(Node):
    """
    Final Brain Node (Pi 5)
    - High-level orchestration using LangGraph-style logic
    - Manages the lifecycle of a request
    """

    def __init__(self):
        super().__init__('langgraph_brain')

        # Subscriptions
        self.input_sub = self.create_subscription(String, '/user_input', self._on_input, 10)
        
        # Publishers
        self.vision_pub = self.create_publisher(VisionQuery, '/vision_query', 10)
        self.move_pub = self.create_publisher(String, '/movement_cmd', 10)
        self.speech_pub = self.create_publisher(String, '/robot_speech', 10)

        self.get_logger().info("🧠 LangGraph Brain v2 (Ready for Sub-Agents)")

    def _on_input(self, msg):
        text = msg.data.lower()
        
        # --- LangGraph Node Logic ---
        # 1. PLAN: Decide what to do
        plan = self._plan_node(text)
        
        # 2. ACT: Execute the plan
        self._execute_node(plan)

    def _plan_node(self, text: str) -> dict:
        self.get_logger().info(f"Planning for: {text}")
        
        if "find" in text or "look for" in text:
            target = text.split("find")[-1].strip() if "find" in text else text.split("look for")[-1].strip()
            return {"action": "vision", "target": target}
        
        elif "go to" in text:
            target = text.split("go to")[-1].strip()
            return {"action": "navigate", "target": target}
            
        elif "back up" in text:
            return {"action": "move_manual", "cmd": "B:20"}
            
        return {"action": "speak", "text": "I'm not sure how to do that yet."}

    def _execute_node(self, plan: dict):
        action = plan.get("action")
        
        if action == "vision":
            msg = VisionQuery()
            msg.target_object = plan["target"]
            msg.use_vlm = True
            self.vision_pub.publish(msg)
            self._speak(f"Starting visual search for {plan['target']}")
            
        elif action == "navigate":
            # First find, then track
            msg = VisionQuery()
            msg.target_object = plan["target"]
            msg.use_vlm = True
            self.vision_pub.publish(msg)
            self._speak(f"Navigating to the {plan['target']}. Locking target now.")

        elif action == "move_manual":
            msg = String()
            msg.data = plan["cmd"]
            self.move_pub.publish(msg)
            self._speak("Executing manual movement.")
            
        elif action == "speak":
            self._speak(plan["text"])

    def _speak(self, text):
        msg = String()
        msg.data = text
        self.speech_pub.publish(msg)

def main(args=None):
    rclpy.init(args=args)
    node = LangGraphBrain()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
