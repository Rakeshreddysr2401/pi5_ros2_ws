#!/usr/bin/env python3
"""How much of the robot's own voice comes back through its mic?

    source /opt/ros/jazzy/setup.bash && source install/setup.bash
    python3 scripts/aec_probe.py                 # record the default mic
    PIPEWIRE_NODE="Echo Cancel Source" python3 scripts/aec_probe.py   # via the AEC

Needs langrobo-voice running (it plays the sentence through the real TTS
path) and a QUIET room for ~15 s. Records the mic for 3 s of silence, then
while the robot speaks a sentence, then 3 s after, and reports per segment:
RMS (how loud), the VAD "speech" ratio (would stt_node think someone is
talking?), and the leak in dB over the silent floor. Regression check for
every audio-path change — VOICE_ROADMAP.md Phase 0b.
"""

import argparse
import sys
import threading
import time

import numpy as np
import rclpy
import sounddevice as sd
import webrtcvad
from rclpy.node import Node
from std_msgs.msg import Bool, String

RATE = 16000
FRAME = 480          # 30 ms
SENTENCE = ("Testing the echo path. I am speaking at my normal volume so we can "
            "measure how much of my own voice comes back through the microphone.")


class Probe(Node):
    def __init__(self):
        super().__init__("aec_probe")
        self.speaking = False
        self.speaking_seen = threading.Event()
        self.pub = self.create_publisher(String, "/voice/robot_speech", 10)
        self.create_subscription(Bool, "/voice/tts_speaking", self._on_speaking, 10)

    def _on_speaking(self, msg):
        self.speaking = bool(msg.data)
        if self.speaking:
            self.speaking_seen.set()


def stats(pcm: np.ndarray, vad) -> tuple[float, float]:
    if len(pcm) < FRAME:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean((pcm.astype(np.float32) / 32768.0) ** 2)))
    n = len(pcm) // FRAME
    voiced = sum(vad.is_speech(pcm[i * FRAME:(i + 1) * FRAME].tobytes(), RATE) for i in range(n))
    return rms, voiced / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sentence", default=SENTENCE)
    ap.add_argument("--settle", type=float, default=3.0, help="silence recorded before/after")
    ap.add_argument("--save", help="write the 'robot talks' mic segment to this WAV (talk over the "
                    "robot, then transcribe it: does your voice get through, are the words right?)")
    args = ap.parse_args()

    rclpy.init()
    node = Probe()
    spinner = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spinner.start()

    dev = next((i for i, d in enumerate(sd.query_devices())
                if d["name"] == "pipewire" and d["max_input_channels"] > 0), None)
    chunks: list[tuple[float, np.ndarray]] = []

    def cb(indata, frames, t, status):
        chunks.append((time.monotonic(), indata[:, 0].copy()))

    print("Keep quiet for ~15 s..." if not args.save else
          "Stay quiet until the robot starts talking, then TALK OVER IT (a full sentence).", flush=True)
    stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                            device=dev, callback=cb)
    stream.start()
    t0 = time.monotonic()
    time.sleep(args.settle)
    t_pub = time.monotonic()
    node.pub.publish(String(data=args.sentence))
    node.pub.publish(String(data="<|eou|>"))
    if not node.speaking_seen.wait(timeout=20.0):
        print("tts_speaking never went true — is langrobo-voice running?", file=sys.stderr)
        stream.stop()
        rclpy.shutdown()
        sys.exit(1)
    t_on = time.monotonic()
    while node.speaking:
        time.sleep(0.05)
    t_off = time.monotonic()
    time.sleep(args.settle)
    stream.stop()
    stream.close()

    def segment(a, b):
        return np.concatenate([c for ts, c in chunks if a <= ts < b]) if chunks else np.zeros(0, np.int16)

    vad = webrtcvad.Vad(2)
    before = segment(t0, t_pub)
    during = segment(t_on + 0.3, t_off)          # skip the first 300 ms (device buffer)
    after = segment(t_off + 0.2, time.monotonic())
    rb, vb = stats(before, vad)
    rd, vd = stats(during, vad)
    ra, va = stats(after, vad)
    leak_db = 20 * np.log10(max(rd, 1e-6) / max(rb, 1e-6))

    print(f"\nrecorded via device {dev} (PIPEWIRE_NODE target, if any, is in the env)")
    print(f"robot spoke for {t_off - t_on:.1f} s (synthesis took {t_on - t_pub:.1f} s)")
    print(f"{'segment':<12}{'rms':>9}{'vad speech':>12}")
    print(f"{'silence':<12}{rb:>9.4f}{vb:>11.0%}")
    print(f"{'robot talks':<12}{rd:>9.4f}{vd:>11.0%}")
    print(f"{'after':<12}{ra:>9.4f}{va:>11.0%}")
    print(f"\nleak: {leak_db:+.1f} dB over the silent floor; stt_node's gate is rms 0.05 "
          f"-> {'WOULD pass as speech' if rd >= 0.05 and vd >= 0.35 else 'stays under the gate'}")
    if args.save:
        import wave
        with wave.open(args.save, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
            w.writeframes(during.tobytes())
        print(f"saved the 'robot talks' segment to {args.save}")
    rclpy.shutdown()


if __name__ == "__main__":
    main()
