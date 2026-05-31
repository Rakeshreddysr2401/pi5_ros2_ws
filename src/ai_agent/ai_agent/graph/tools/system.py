from typing import Optional

from langchain_core.tools import tool

from . import _bridge


@tool
def set_active_order(order_id: Optional[str]) -> str:
    """Store or clear the active Swiggy order ID for background delivery polling.

    Call with the order_id string after placing an order so the robot monitors
    delivery status every 2 minutes. Call with None once the order is delivered
    to stop polling."""
    _bridge.get().set_active_order(order_id)
    if order_id:
        return f"Now monitoring order {order_id} for delivery"
    return "Order monitoring cleared"


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
