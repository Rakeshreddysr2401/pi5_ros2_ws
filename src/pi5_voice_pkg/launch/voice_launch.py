"""Bring up Pi5-local STT+TTS. Only run this while the Jetson's ai_stack
voice role is stopped (fleet_role.sh voice stop) — two tts_node/stt_node
pairs on the same /voice/* topics would double-speak and double-transcribe.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    params = os.path.join(get_package_share_directory('pi5_voice_pkg'), 'config', 'voice_params.yaml')
    return LaunchDescription([
        Node(package='pi5_voice_pkg', executable='tts_node', name='pi5_tts_node',
             parameters=[params], output='screen'),
        Node(package='pi5_voice_pkg', executable='stt_node', name='pi5_stt_node',
             parameters=[params], output='screen'),
    ])
