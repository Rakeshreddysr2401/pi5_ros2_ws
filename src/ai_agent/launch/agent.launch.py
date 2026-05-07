import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    config = os.path.join(
        get_package_share_directory("ai_agent"), "config", "agent_params.yaml"
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "base_url",
            default_value="http://192.168.31.24:8080/v1",
            description="llama.cpp server URL on Mac Mini",
        ),
        DeclareLaunchArgument(
            "provider",
            default_value="llamacpp",
            description="LLM provider: llamacpp | openai | anthropic | gemini | ollama",
        ),
        Node(
            package="ai_agent",
            executable="agent_node",
            name="agent_node",
            parameters=[
                config,
                {
                    "base_url": LaunchConfiguration("base_url"),
                    "provider": LaunchConfiguration("provider"),
                },
            ],
            output="screen",
        ),
    ])
