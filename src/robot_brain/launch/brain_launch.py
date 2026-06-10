import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    agent_launch = os.path.join(
        get_package_share_directory("ai_agent"), "launch", "agent.launch.py"
    )

    return LaunchDescription([
        # ── Launch arguments ─────────────────────────────────────────────────
        DeclareLaunchArgument(
            "base_url",
            default_value="http://singireddys.local:8080/v1",
            description="Mac Mini llama.cpp server URL",
        ),
        DeclareLaunchArgument(
            "provider",
            default_value="llamacpp",
            description="LLM provider: llamacpp | openai | anthropic | gemini | ollama",
        ),
        DeclareLaunchArgument(
            "agent_port",
            default_value="8888",
            description="UDP port for micro-ROS agent (ESP32 connects here)",
        ),

        # ── micro-ROS agent (Pi5 ↔ ESP32 WiFi bridge) ───────────────────────
        # ESP32 connects to Pi5 IP:8888 over WiFi UDP.
        # Bridges /cmd_vel (Twist) → ESP32 wheels.
        # Built from source in ~/microros_ws — see INTEGRATION.md
        Node(
            package="micro_ros_agent",
            executable="micro_ros_agent",
            name="micro_ros_agent",
            arguments=["udp4", "--port", LaunchConfiguration("agent_port")],
            output="screen",
        ),

        # ── LangGraph brain ──────────────────────────────────────────────────
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(agent_launch),
            launch_arguments={
                "base_url": LaunchConfiguration("base_url"),
                "provider": LaunchConfiguration("provider"),
            }.items(),
        ),
    ])
