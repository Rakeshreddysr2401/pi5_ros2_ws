from typing import TypedDict, Optional, List, Annotated
import operator

class WorldState(TypedDict):
    # The raw text from the user
    user_input: str
    # High-level task description
    current_task: Optional[str]
    # The target object being sought
    target_object: Optional[str]
    # History of tool calls and results
    history: Annotated[List[str], operator.add]
    # Whether the goal has been reached
    is_goal_reached: bool
    # Final response to speak back to the user
    final_response: Optional[str]
