#!/usr/bin/env python3
"""Interactive TTS tester: type a line, hear the Pi5 TTS node speak it.

Publishes each typed line to /voice/robot_speech (exactly what agent_node's
reply stream uses), then the end-of-utterance marker so the node's
/voice/tts_speaking flag toggles back to false like in production.

Run in a second terminal while ./run_tts.sh is up. Ctrl-D or 'quit' to exit.
Requires: source /opt/ros/jazzy/setup.bash && source install/setup.bash
"""
import rclpy
from std_msgs.msg import String

EOU = "<|eou|>"  # matches tts_node.SPEECH_EOU / langrobo_core speech_stream


def main():
    rclpy.init()
    node = rclpy.create_node("tts_say")
    pub = node.create_publisher(String, "/voice/robot_speech", 10)
    print("Type text to speak (Ctrl-D or 'quit' to exit):")
    try:
        while True:
            try:
                line = input("say> ")
            except EOFError:
                break
            if line.strip().lower() in ("quit", "exit"):
                break
            if not line.strip():
                continue
            pub.publish(String(data=line))
            pub.publish(String(data=EOU))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
