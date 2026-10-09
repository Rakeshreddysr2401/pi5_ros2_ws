"""LangRobo brain launch — micro-ROS agent (ESP32 bridge) + agent_node.

Standalone:   ros2 launch langrobo_ros brain_launch.py
Under systemd the micro-ROS agent runs as its own unit (restartable without
killing the brain), so the brain unit passes start_micro_ros:=false.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
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
            "esp32_dev",
            default_value="/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0",
            description="ESP32's USB serial port (micro-ROS agent, 115200 baud)",
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

        # ── micro-ROS agent (Pi5 ↔ ESP32 USB serial bridge) ─────────────────
        # ESP32 on the Pi5's USB cable since 2026-10-07; bridges /cmd_vel → wheels.
        # The SAME script the langrobo-microros unit runs: it picks the XRCE
        # agent build and resets the ESP32 first, without which a restarted
        # agent never gets the board back (run_microros.sh).
        ExecuteProcess(
            cmd=[os.path.join(os.path.expanduser("~"), "ros2_ws", "scripts", "run_microros.sh"),
                 "serial", LaunchConfiguration("esp32_dev")],
            name="micro_ros_agent",
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
