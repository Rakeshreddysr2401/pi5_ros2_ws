#!/usr/bin/env python3
"""Live mic wake-word scorer — watch the wake score in real time as you speak.

Usage:
    # Test a custom model:
    python3 scripts/wake_live_score.py src/langrobo_ros/models/wake/mitra.onnx

    # Test with threshold:
    python3 scripts/wake_live_score.py src/langrobo_ros/models/wake/mitra.onnx --threshold 0.45

    # Test with a personal verifier attached:
    python3 scripts/wake_live_score.py src/langrobo_ros/models/wake/mitra.onnx --verifier src/langrobo_ros/models/wake/mitra_verifier.pkl

    # Test built-in stock model (e.g. hey_jarvis) without a custom file:
    python3 scripts/wake_live_score.py hey_jarvis

Needs: pip install openwakeword sounddevice numpy
"""

import argparse
import os
import sys
import time

import numpy as np

CHUNK = 1280  # 80 ms at 16 kHz
RATE = 16000


def render_bar(score: float, threshold: float, width: int = 25) -> str:
    filled = int(min(max(score, 0.0), 1.0) * width)
    bar = "=" * filled + " " * (width - filled)
    marker = "*** WAKE ***" if score >= threshold else "            "
    return f"[{bar}] {score:5.2f} {marker}"


def main():
    ap = argparse.ArgumentParser(description="Live mic scoring for openWakeWord")
    ap.add_argument("model", help="Path to .onnx model or built-in model name (e.g. hey_jarvis)")
    ap.add_argument("--threshold", type=float, default=0.5, help="Wake trigger threshold (default: 0.5)")
    ap.add_argument("--verifier", default="", help="Path to personal verifier .pkl (optional)")
    ap.add_argument("--verifier-threshold", type=float, default=0.3, help="Verifier threshold (default: 0.3)")
    ap.add_argument("--device", default=None, help="Input audio device index or name (default: system mic)")
    args = ap.parse_args()

    import sounddevice as sd
    from openwakeword.model import Model

    model_arg = args.model
    stem = os.path.splitext(os.path.basename(model_arg))[0]

    kwargs = {}
    if args.verifier:
        if not os.path.exists(args.verifier):
            print(f"Error: verifier file not found: {args.verifier}", file=sys.stderr)
            return 1
        kwargs = {
            "custom_verifier_models": {stem: args.verifier},
            "custom_verifier_threshold": args.verifier_threshold,
        }

    try:
        if os.path.exists(model_arg):
            model = Model(wakeword_model_paths=[model_arg], **kwargs)
        else:
            model = Model(wakeword_models=[model_arg], **kwargs)
    except Exception as e:
        print(f"Failed to load wake model '{model_arg}': {e}", file=sys.stderr)
        return 1

    key = list(model.models.keys())[0]

    dev_info = sd.query_devices(args.device if args.device is not None else sd.default.device[0])
    print(f"\nModel:      {key}")
    print(f"Verifier:   {args.verifier or 'None'}")
    print(f"Threshold:  {args.threshold:.2f}")
    print(f"Microphone: {dev_info['name']}")
    print("\n" + "=" * 75)
    print("  LIVE CALL MONITOR ACTIVE — Listening continuously")
    print("  Speak from different distances / say different words to test.")
    print("  Every call will be logged below with its exact peak score and result.")
    print("=" * 75 + "\n")

    call_count = 0
    in_speech = False
    speech_frames = 0
    silence_frames = 0
    utterance_scores = []
    utterance_rms = []
    wake_fired_for_utterance = False
    session_peak = 0.0

    try:
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK, device=args.device) as stream:
            while True:
                audio, _ = stream.read(CHUNK)
                pcm = audio.flatten()
                score = float(model.predict(pcm)[key])
                session_peak = max(session_peak, score)

                # Compute frame RMS to detect speech vs silence
                rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) / 32768.0

                is_voice = (rms > 0.012) or (score > 0.08)

                if is_voice:
                    in_speech = True
                    speech_frames += 1
                    silence_frames = 0
                    utterance_scores.append(score)
                    utterance_rms.append(rms)

                    if score >= args.threshold and not wake_fired_for_utterance:
                        wake_fired_for_utterance = True
                elif in_speech:
                    silence_frames += 1
                    utterance_scores.append(score)
                    utterance_rms.append(rms)

                    # 400ms of silence marks end of spoken utterance (5 chunks * 80ms)
                    if silence_frames >= 5:
                        if speech_frames >= 3:
                            call_count += 1
                            peak_s = max(utterance_scores)
                            peak_r = max(utterance_rms)
                            timestamp = time.strftime("%H:%M:%S")

                            if wake_fired_for_utterance or peak_s >= args.threshold:
                                status = ">>> WAKE TRIGGERED (SUCCESS) <<<"
                            else:
                                status = "--- IGNORED (NO WAKE) ---"

                            # Clear the live line and print permanent log
                            print(f"\r[{timestamp}] CALL #{call_count:02d} | Peak Score: {peak_s:5.3f} | Mic Level: {peak_r:5.3f} | {status}")
                            model.reset()

                        in_speech = False
                        speech_frames = 0
                        silence_frames = 0
                        utterance_scores = []
                        utterance_rms = []
                        wake_fired_for_utterance = False
                else:
                    # Live rolling display while waiting
                    bar_str = render_bar(score, args.threshold)
                    print(f"\r{bar_str} (session peak: {session_peak:5.2f}) | Listening...", end="", flush=True)

    except KeyboardInterrupt:
        print(f"\n\nSession ended. Total calls evaluated: {call_count}, Session peak: {session_peak:.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
