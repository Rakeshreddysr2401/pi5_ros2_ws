#!/usr/bin/env python3
"""Watch the robot's own listening, live: what it heard, from which provider, how fast.

    source install/setup.bash && python3 scripts/voice_lab/watch_stt.py

One block per sentence, from stt_node's topics (nothing is sent anywhere):
  /voice/debug_vad         a sentence was cut, or dropped and why
  /voice/stt_meta          provider, speech length, recogniser time, fallback
  /voice/debug_transcript  the text (Sarvam translates Telugu to English)
"""
import json
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String


class Watch(Node):
    def __init__(self):
        super().__init__('watch_stt')
        self._pending = None
        self.create_subscription(String, '/voice/debug_vad', self._vad, 10)
        self.create_subscription(String, '/voice/stt_meta', self._on_meta, 10)
        self.create_subscription(String, '/voice/debug_transcript', self._text, 10)
        print('Watching the robot\'s mic -- speak (Ctrl+C to quit)\n', flush=True)

    def _vad(self, msg):
        if msg.data.startswith('utterance detected'):
            print(f"{time.strftime('%H:%M:%S')}  sentence cut, sending...", flush=True)
        elif msg.data.startswith('dropped'):
            print(f"{time.strftime('%H:%M:%S')}  dropped before sending: {msg.data[9:]}", flush=True)

    # stt_node publishes the transcript FIRST and its stt_meta right after,
    # so hold the text and print when the meta for it arrives.
    def _text(self, msg):
        self._pending = msg.data

    def _on_meta(self, msg):
        try:
            m = json.loads(msg.data)
        except ValueError:
            m = {}
        text, self._pending = getattr(self, '_pending', None), None
        if text is None:
            return
        how = (f"{m.get('provider', '?')}"
               + (f" (FELL BACK: {m.get('fallback_reason', '')})" if m.get('fell_back') else '')
               + f" | {m.get('audio_ms', 0) / 1000:.1f} s of speech | recogniser {m.get('latency_ms', '?')} ms")
        print(f"          heard : {text}\n          via   : {how}\n", flush=True)


def main():
    rclpy.init()
    try:
        rclpy.spin(Watch())
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
