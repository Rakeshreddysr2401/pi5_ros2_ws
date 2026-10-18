#!/usr/bin/env python3
"""Interactive microphone test for KINO wake word.

Records a short snippet of live audio from your microphone, runs it
through the trained `kino.onnx` model, and prints a timeline of scores.

Usage:
    ./.venv/bin/python scripts/test_mic_kino.py [--duration 5] [--threshold 0.45]
"""

import argparse
import time
import numpy as np
import sounddevice as sd
from openwakeword.model import Model

RATE = 16000
CHUNK = 1280


def render_bar(score: float, threshold: float, width: int = 24) -> str:
    filled = int(min(max(score, 0.0), 1.0) * width)
    bar = "=" * filled + " " * (width - filled)
    status = "🔥 *** WAKE DETECTED! ***" if score >= threshold else ""
    return f"[{bar}] {score:5.2f}  {status}"


def main():
    parser = argparse.ArgumentParser(description="Test KINO wake word live on mic")
    parser.add_argument("--duration", type=float, default=5.0, help="Recording duration in seconds (default: 5.0)")
    parser.add_argument("--threshold", type=float, default=0.45, help="Wake threshold (default: 0.45)")
    parser.add_argument("--model", default="src/langrobo_ros/models/wake/kino.onnx", help="Path to model")
    args = parser.parse_args()

    print("\n" + "=" * 65)
    print("  KINO Wake-Word Live Microphone Test")
    print("=" * 65)

    oww = Model(wakeword_models=[args.model])
    key = list(oww.models.keys())[0]

    print(f"\nModel: {args.model}")
    print(f"Wake Key: '{key}', Threshold: {args.threshold}\n")
    print("Get ready to speak into your microphone...")
    for i in [3, 2, 1]:
        print(f"  Starting in {i}...")
        time.sleep(1)

    print("\n🔴 RECORDING NOW! Say: 'Hey Kino' (or try other phrases)")
    print(f"   Listening for {args.duration:.1f} seconds...\n")

    total_samples = int(args.duration * RATE)
    audio = sd.rec(total_samples, samplerate=RATE, channels=1, dtype="int16")
    sd.wait()
    audio = audio.flatten()

    import scipy.io.wavfile as wavfile
    wavfile.write("/tmp/last_test_mic.wav", RATE, audio)
    print("⏹️  Recording complete. Analyzing audio stream...\n")

    oww.reset()
    scores = []
    times = []

    for i in range(0, len(audio) - CHUNK + 1, CHUNK):
        chunk = audio[i:i + CHUNK]
        t_sec = i / RATE
        pred = oww.predict(chunk)
        sc = float(pred[key])
        scores.append(sc)
        times.append(t_sec)

    peak_score = max(scores) if scores else 0.0
    peak_idx = scores.index(peak_score) if scores else 0
    peak_time = times[peak_idx] if times else 0.0

    print("--- TIMELINE BREAKDOWN ---")
    step = max(len(scores) // 15, 1)
    for idx in range(0, len(scores), step):
        t = times[idx]
        sc = scores[idx]
        print(f"  {t:4.1f}s: {render_bar(sc, args.threshold)}")

    rms = np.sqrt(np.mean(audio.astype(np.float32)**2))
    print("\n" + "-" * 65)
    print(f"  Peak Wake Score:  {peak_score:.3f}  (at {peak_time:.2f}s)")
    print(f"  Audio Level (RMS): {rms:.1f}  {'[Volume OK]' if rms > 40 else '[Low Volume - speak closer/louder]'}")

    if peak_score >= args.threshold:
        print("  Result:           ✅ SUCCESS! Robot would wake up and answer.")
    else:
        print(f"  Result:           ❌ Did not trigger (peak {peak_score:.3f} < threshold {args.threshold}).")
    print("-" * 65 + "\n")


if __name__ == "__main__":
    main()
