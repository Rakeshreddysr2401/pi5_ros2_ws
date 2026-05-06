import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    agent_config = os.path.join(
        get_package_share_directory("ai_agent"), "config", "agent_params.yaml"
    )
    agent_launch = os.path.join(
        get_package_share_directory("ai_agent"), "launch", "agent.launch.py"
    )

    return LaunchDescription([
        # ── Launch arguments ─────────────────────────────────────────────────
        DeclareLaunchArgument(
            "base_url",
            default_value="http://192.168.1.100:8080/v1",
            description="Mac Mini llama.cpp server URL",
        ),
        DeclareLaunchArgument(
            "provider",
            default_value="llamacpp",
            description="LLM provider: llamacpp | openai | anthropic | gemini | ollama",
        ),

        # ── LangGraph brain ──────────────────────────────────────────────────
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(agent_launch),
            launch_arguments={
                "base_url": LaunchConfiguration("base_url"),
                "provider": LaunchConfiguration("provider"),
            }.items(),
        ),

        # ── Chassis pilot (drives /cmd_vel → ESP32 micro-ROS2) ──────────────
        Node(
            package="robot_brain",
            executable="chassis_pilot",
            name="chassis_pilot",
            parameters=[{
                "kp_yaw": 0.5,
                "forward_speed_cm_s": 12.0,
                "turn_speed_deg_s": 180.0,
            }],
            output="screen",
        ),
    ])
