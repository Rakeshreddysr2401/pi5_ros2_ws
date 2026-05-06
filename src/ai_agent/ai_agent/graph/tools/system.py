from langchain_core.tools import tool

from . import _bridge


@tool
def get_robot_status() -> str:
    """Get the robot's current operational status: battery level, active task,
    and hardware state.

    Calls the /robot/get_status ROS2 service.  Falls back to a basic message
    if the service is not available (e.g. ESP32 not yet connected)."""
    try:
        from std_srvs.srv import Trigger
        resp = _bridge.get().call_service(
            "/robot/get_status", Trigger, Trigger.Request(), timeout=3.0
        )
        return resp.message if resp.success else "Status service returned failure"
    except TimeoutError:
        return "Status: operational — status service not available (ESP32 not connected yet)"
    except Exception as e:
        return f"Status: operational — could not reach status service ({e})"


@tool
def ros2_publish(topic: str, data: str) -> str:
    """Publish a string message to any ROS2 topic.

    Use for hardware not covered by the other tools.

    Known topics:
      /movement_cmd   — chassis commands, e.g. 'F:20'  (prefer move_robot)
      /arm/joint_goal — joint angles, e.g. '{\"j1\": 90}'
      /gripper/cmd    — 'open' or 'close'"""
    _bridge.get().publish_to_topic(topic, data)
    return f"Published '{data}' → {topic}"
