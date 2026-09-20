#!/usr/bin/env python3
"""Record your own wake-word clips (and some non-wake speech) for training.

    python3 scripts/wake_record_clips.py positive  --out data/mitra_pos --n 30
    python3 scripts/wake_record_clips.py negative  --out data/mitra_neg --n 20

Runs on any machine with a mic (laptop or the Pi): needs only numpy +
sounddevice (pip install sounddevice numpy). Writes 16 kHz mono 16-bit WAVs,
the format openWakeWord training and the personal verifier expect. See
WAKE_WORD_INTEGRATION.md for how the clips are used.

positive : say ONLY the wake word ("Mitra") once per clip, in different
           tones, distances and speeds — a few whispered, a few from across
           the room, a few fast.
negative : say anything that is NOT the wake word — names that sound
           similar ("Mithun", "meter", "Mitali"), normal sentences, TV.
"""

import argparse
import os
import sys
import time
import wave

import numpy as np
import sounddevice as sd

RATE = 16000


def record(seconds: float, device=None) -> np.ndarray:
    audio = sd.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="int16", device=device)
    sd.wait()
    return audio[:, 0]


def save(path: str, pcm: np.ndarray) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["positive", "negative"])
    ap.add_argument("--out", required=True, help="folder for the WAVs")
    ap.add_argument("--n", type=int, default=30, help="how many clips")
    ap.add_argument("--seconds", type=float, default=None,
                    help="clip length (default 2.0 for positive, 4.0 for negative)")
    ap.add_argument("--device", default=None, help="sounddevice input name/index (default: system mic)")
    args = ap.parse_args()
    seconds = args.seconds or (2.0 if args.kind == "positive" else 4.0)
    os.makedirs(args.out, exist_ok=True)
    existing = len([f for f in os.listdir(args.out) if f.endswith(".wav")])

    print(f"mic: {sd.query_devices(args.device or sd.default.device[0])['name']}")
    if args.kind == "positive":
        print(f"Say ONLY the wake word, once, when you see GO. {args.n} clips of {seconds:.0f}s.")
    else:
        print(f"Say anything EXCEPT the wake word when you see GO. {args.n} clips of {seconds:.0f}s.")
    print("Vary it: normal / soft / far away / fast / with background noise. Ctrl-C to stop early.\n")
    try:
        for i in range(args.n):
            n = existing + i + 1
            for t in (3, 2, 1):
                print(f"  clip {n}: ready in {t}...", end="\r", flush=True)
                time.sleep(0.7)
            print(f"  clip {n}: GO            ", flush=True)
            pcm = record(seconds, args.device)
            peak = float(np.abs(pcm).max()) / 32768.0
            path = os.path.join(args.out, f"{args.kind}_{n:03d}.wav")
            if peak < 0.02:
                print(f"      too quiet (peak {peak:.3f}) — not saved, speak closer/louder")
                continue
            save(path, pcm)
            print(f"      saved {path} (peak {peak:.2f})")
    except KeyboardInterrupt:
        print("\nstopped.")
    total = len([f for f in os.listdir(args.out) if f.endswith(".wav")])
    print(f"\n{total} clips in {args.out}")


if __name__ == "__main__":
    sys.exit(main())
