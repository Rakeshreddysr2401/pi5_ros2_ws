from typing import TypedDict, Optional, Dict, Any

class WorldState(TypedDict):
    user_input: Optional[str]
    perception: Dict[str, Any]
    robot_pose: Dict[str, float]
    current_task: Optional[str]
    last_tool_result: Optional[Dict[str, Any]]
    final_response: Optional[str]