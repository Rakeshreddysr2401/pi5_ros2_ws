"""Pi5 TTS — Kokoro-onnx on CPU, same /voice/* wire protocol as the Jetson's tts_node.

Measured on this Pi5 (Cortex-A76 @ 2.4GHz, 4 cores), 2026-09-04:
  fp32 model, 4 intra-op threads: RTF ~1.8 (synthesis takes ~1.8x the audio's
  own length). The int8 quantized model is WORSE here (RTF ~3.75) — ARM NEON
  has no fast int8 path for this op set, unlike x86 VNNI. Use fp32.

RTF 1.8 means: it cannot keep up with continuous speech, but the existing
wire protocol already sends one sentence per message (speech_stream.py), so
each reply sentence pays its own ~1.8x delay rather than compounding forever.
"""

import queue
import threading

import numpy as np
import onnxruntime as ort
import rclpy
import sounddevice as sd
from kokoro_onnx import Kokoro
from rclpy.node import Node
from std_msgs.msg import Bool, String

SPEECH_EOU = "<|eou|>"  # must match langrobo_core/utils/speech_stream.py
CHUNK_FRAMES = 2400  # 0.1s @ 24kHz — playback granularity for fast stop


class TTSNode(Node):
    def __init__(self):
        super().__init__('pi5_tts_node')
        self.declare_parameter('model_path', '')
        self.declare_parameter('voices_path', '')
        self.declare_parameter('voice', 'af_heart')
        self.declare_parameter('speed', 1.0)
        self.declare_parameter('output_device', 'Blackwire')
        self.declare_parameter('threads', 4)

        model_path = self.get_parameter('model_path').value
        voices_path = self.get_parameter('voices_path').value
        self.voice = self.get_parameter('voice').value
        self.speed = float(self.get_parameter('speed').value)
        device_hint = self.get_parameter('output_device').value
        threads = int(self.get_parameter('threads').value)

        self._out_device = self._find_device(device_hint, kind='output')
        self.get_logger().info(f'output device: {self._out_device}')

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        sess = ort.InferenceSession(model_path, sess_options=so, providers=['CPUExecutionProvider'])
        self._kokoro = Kokoro.from_session(sess, voices_path)
        self.get_logger().info('kokoro model loaded')

        self._speaking_pub = self.create_publisher(Bool, '/voice/tts_speaking', 10)
        self.create_subscription(String, '/voice/robot_speech', self._on_speech, 10)
        self.create_subscription(String, '/voice/tts_stop', self._on_stop, 10)

        self._q: queue.Queue[str] = queue.Queue()
        self._interrupt = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _find_device(self, hint: str, kind: str):
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            key = 'max_output_channels' if kind == 'output' else 'max_input_channels'
            if hint.lower() in d['name'].lower() and d[key] > 0:
                return i
        self.get_logger().warning(f'no {kind} device matching {hint!r}; using system default')
        return None

    def _on_speech(self, msg: String):
        self._q.put(msg.data)

    def _on_stop(self, msg: String):
        self._interrupt.set()
        with self._q.mutex:
            self._q.queue.clear()

    def _run(self):
        speaking = False
        while rclpy.ok():
            text = self._q.get()
            if text == SPEECH_EOU:
                if speaking:
                    self._speaking_pub.publish(Bool(data=False))
                    speaking = False
                continue
            if self._interrupt.is_set():
                self._interrupt.clear()
                continue
            if not speaking:
                self._speaking_pub.publish(Bool(data=True))
                speaking = True
            self._speak(text)

    def _speak(self, text: str):
        try:
            samples, sr = self._kokoro.create(text, voice=self.voice, speed=self.speed, lang='en-us')
        except Exception:
            self.get_logger().exception(f'synthesis failed for: {text!r}')
            return
        self._play(samples, sr)

    def _play(self, samples: np.ndarray, sr: int):
        try:
            with sd.OutputStream(samplerate=sr, channels=1, dtype='float32', device=self._out_device) as stream:
                for i in range(0, len(samples), CHUNK_FRAMES):
                    if self._interrupt.is_set():
                        break
                    stream.write(samples[i:i + CHUNK_FRAMES])
        except Exception:
            self.get_logger().exception('playback failed')


def main():
    rclpy.init()
    node = TTSNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
