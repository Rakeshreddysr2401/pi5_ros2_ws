from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        # LangGraph Brain
        Node(
            package='ai_agent',
            executable='agent_node',
            name='brain_node'
        ),
        # Chassis Pilot
        Node(
            package='robot_brain',
            executable='chassis_pilot',
            name='chassis_pilot'
        )
    ])
