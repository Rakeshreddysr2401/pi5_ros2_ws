"""LangRobo brain launch — micro-ROS agent (ESP32 bridge) + agent_node.

Standalone:   ros2 launch langrobo_ros brain_launch.py
Under systemd the micro-ROS agent runs as its own unit (restartable without
killing the brain), so the brain unit passes start_micro_ros:=false.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    config = os.path.join(
        get_package_share_directory("langrobo_ros"), "config", "agent_params.yaml"
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "base_url",
            default_value="http://singireddys-mac-mini.local:8080/v1",
            description="llama.cpp server URL on Mac Mini",
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
        DeclareLaunchArgument(
            "start_micro_ros",
            default_value="true",
            description="Also start the micro-ROS agent (false when it runs as its own systemd unit)",
        ),
        DeclareLaunchArgument(
            "robot_body",
            default_value="rover",
            description="cmd_vel target: 'rover' (real ESP32, plain Twist on /cmd_vel) "
                        "or 'sim' (rover_sim, TwistStamped on /mecanum_drive_controller/cmd_vel)",
        ),

        # ── micro-ROS agent (Pi5 ↔ ESP32 WiFi bridge) ───────────────────────
        # ESP32 connects to Pi5 IP:8888 over WiFi UDP; bridges /cmd_vel → wheels.
        # Built from source in ~/microros_ws — see OPERATIONS.md.
        Node(
            package="micro_ros_agent",
            executable="micro_ros_agent",
            name="micro_ros_agent",
            arguments=["udp4", "--port", LaunchConfiguration("agent_port")],
            output="screen",
            condition=IfCondition(LaunchConfiguration("start_micro_ros")),
        ),

        # ── LangGraph brain ──────────────────────────────────────────────────
        Node(
            package="langrobo_ros",
            executable="agent_node",
            name="agent_node",
            parameters=[
                config,
                {
                    "base_url": LaunchConfiguration("base_url"),
                    "provider": LaunchConfiguration("provider"),
                    "robot_body": LaunchConfiguration("robot_body"),
                },
            ],
            output="screen",
        ),
    ])
