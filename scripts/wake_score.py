#!/usr/bin/env python3
"""Score WAV clips against a wake-word model — does it fire on the word, and
does it stay quiet on everything else?

    python3 scripts/wake_score.py models/wake/mitra.onnx data/mitra_pos data/mitra_neg
    python3 scripts/wake_score.py mitra.onnx pos/ neg/ --verifier mitra_verifier.pkl --threshold 0.5

Prints the peak score per clip and a summary: how many positives crossed the
threshold (want ~all) and how many negatives did (want none). Run this on
the laptop BEFORE copying a model to the robot — WAKE_WORD_INTEGRATION.md.
Needs: pip install openwakeword numpy
"""

import argparse
import os
import sys
import wave

import numpy as np

CHUNK = 1280   # 80 ms at 16 kHz, what openWakeWord predicts on


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, \
            f"{path}: need 16 kHz mono 16-bit"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def peak_score(model, key: str, pcm: np.ndarray) -> float:
    model.reset()
    best = 0.0
    # pad with silence so the last word has a full window behind it
    pcm = np.concatenate([pcm, np.zeros(CHUNK * 8, dtype=np.int16)])
    for i in range(0, len(pcm) - CHUNK + 1, CHUNK):
        best = max(best, float(model.predict(pcm[i:i + CHUNK])[key]))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", help="path to the .onnx wake model")
    ap.add_argument("positive_dir")
    ap.add_argument("negative_dir", nargs="?")
    ap.add_argument("--verifier", default="", help="personal verifier .pkl (optional)")
    ap.add_argument("--verifier-threshold", type=float, default=0.3)
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()

    from openwakeword.model import Model
    stem = os.path.splitext(os.path.basename(args.model))[0]
    kwargs = {}
    if args.verifier:
        kwargs = {"custom_verifier_models": {stem: args.verifier},
                  "custom_verifier_threshold": args.verifier_threshold}
    model = Model(wakeword_model_paths=[args.model], **kwargs)
    key = list(model.models.keys())[0]

    def run(folder, label):
        files = sorted(f for f in os.listdir(folder) if f.endswith(".wav"))
        scores = []
        print(f"\n{label} ({folder}):")
        for f in files:
            s = peak_score(model, key, load_wav(os.path.join(folder, f)))
            scores.append(s)
            mark = "FIRE" if s >= args.threshold else "    "
            print(f"  {mark} {s:5.2f}  {f}")
        return scores

    pos = run(args.positive_dir, "wake word clips")
    hit = sum(s >= args.threshold for s in pos)
    print(f"\n  fired on {hit}/{len(pos)} wake-word clips at threshold {args.threshold} "
          f"(median peak {np.median(pos):.2f})" if pos else "  no positive clips")
    if args.negative_dir:
        neg = run(args.negative_dir, "NOT the wake word")
        false = sum(s >= args.threshold for s in neg)
        print(f"\n  false fires on {false}/{len(neg)} other clips "
              f"(highest {max(neg):.2f})" if neg else "  no negative clips")
        if pos and neg:
            print(f"\n  a safe threshold sits between the highest negative ({max(neg):.2f}) "
                  f"and the lowest positive you care about ({min(pos):.2f})")


if __name__ == "__main__":
    sys.exit(main())
