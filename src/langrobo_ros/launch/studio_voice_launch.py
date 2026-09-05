"""Voice for dev mode: the pi5 STT/TTS pair plus the Studio bridge node.

    ros2 launch langrobo_ros studio_voice_launch.py

Expects `./scripts/dev.sh` already running in another terminal (it owns the
graph on :2024 and the micro-ROS agent). NEVER run this while langrobo-brain
is up — agent_node subscribes the same /voice/user_input and you would get two
brains answering one utterance.

Arguments:
    voice:=false          bring only the bridge (voice pair already running)
    studio_url:=…         non-default server
    thread_mode:=per_turn fresh conversation per utterance
    stream_speech:=false  speak the whole reply at the end instead of streaming
    watch_ui:=false       stop speaking turns typed into the Studio box
    thread_id:=…          pin to an existing conversation (id from the Studio URL)
                          so typed and spoken turns share one history
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    voice_share = get_package_share_directory('pi5_voice_pkg')

    args = [
        DeclareLaunchArgument('voice',         default_value='true'),
        DeclareLaunchArgument('studio_url',    default_value='http://127.0.0.1:2024'),
        DeclareLaunchArgument('assistant_id',  default_value='agent'),
        DeclareLaunchArgument('thread_mode',   default_value='persistent'),
        DeclareLaunchArgument('stream_speech', default_value='true'),
        DeclareLaunchArgument('watch_ui',      default_value='true'),
        # Empty = the bridge creates its own thread.
        DeclareLaunchArgument('thread_id',     default_value=''),
    ]

    voice_pair = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(voice_share, 'launch', 'voice_launch.py')),
        condition=IfCondition(LaunchConfiguration('voice')),
    )

    bridge = Node(
        package='langrobo_ros', executable='studio_voice_node',
        name='studio_voice_node', output='screen',
        parameters=[{
            'studio_url':    LaunchConfiguration('studio_url'),
            'assistant_id':  LaunchConfiguration('assistant_id'),
            'thread_mode':   LaunchConfiguration('thread_mode'),
            'stream_speech': LaunchConfiguration('stream_speech'),
            'watch_ui':      LaunchConfiguration('watch_ui'),
            'thread_id':     LaunchConfiguration('thread_id'),
        }],
    )

    return LaunchDescription(args + [voice_pair, bridge])
