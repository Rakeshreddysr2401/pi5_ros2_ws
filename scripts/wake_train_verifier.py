#!/usr/bin/env python3
"""Train a personal verifier model on your real voice clips locally.

Usage:
    python3 scripts/wake_train_verifier.py \
        --model src/langrobo_ros/models/wake/mitra.onnx \
        --pos ~/wake_data/mitra_pos \
        --neg ~/wake_data/mitra_neg \
        --out src/langrobo_ros/models/wake/mitra_verifier.pkl

Needs: pip install openwakeword scikit-learn scipy
"""

import argparse
import glob
import os
import sys


def main():
    ap = argparse.ArgumentParser(description="Train openWakeWord personal verifier on real clips")
    ap.add_argument("--model", required=True, help="Path to base .onnx wake model")
    ap.add_argument("--pos", required=True, help="Directory containing positive WAV clips (you saying the wake word)")
    ap.add_argument("--neg", required=True, help="Directory containing negative WAV clips (other words/speech)")
    ap.add_argument("--out", default="", help="Output path for .pkl verifier (default: alongside model)")
    args = ap.parse_args()

    model_path = os.path.abspath(args.model)
    if not os.path.exists(model_path):
        print(f"Error: Base model not found: {model_path}", file=sys.stderr)
        return 1

    pos_dir = os.path.expanduser(args.pos)
    neg_dir = os.path.expanduser(args.neg)

    pos_clips = sorted(glob.glob(os.path.join(pos_dir, "*.wav")))
    neg_clips = sorted(glob.glob(os.path.join(neg_dir, "*.wav")))

    if not pos_clips:
        print(f"Error: No .wav clips found in positive directory: {pos_dir}", file=sys.stderr)
        return 1
    if not neg_clips:
        print(f"Error: No .wav clips found in negative directory: {neg_dir}", file=sys.stderr)
        return 1

    out_path = args.out
    if not out_path:
        base, _ = os.path.splitext(model_path)
        out_path = f"{base}_verifier.pkl"
    out_path = os.path.abspath(os.path.expanduser(out_path))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print(f"\nBase model:       {model_path}")
    print(f"Positive clips:   {len(pos_clips)} files from {pos_dir}")
    print(f"Negative clips:   {len(neg_clips)} files from {neg_dir}")
    print(f"Target output:    {out_path}")
    print("\nExtracting features and training verifier (runs locally in ~5-15s)...")

    try:
        from openwakeword.custom_verifier_model import train_custom_verifier
        train_custom_verifier(
            positive_reference_clips=pos_clips,
            negative_reference_clips=neg_clips,
            output_path=out_path,
            model_name=model_path,
        )
    except Exception as e:
        print(f"Training failed: {e}", file=sys.stderr)
        return 1

    if not os.path.exists(out_path):
        print(f"Error: Output file {out_path} was not created.", file=sys.stderr)
        return 1

    size_kb = os.path.getsize(out_path) / 1024.0
    print(f"\nSuccessfully trained personal verifier: {out_path} ({size_kb:.1f} KB)")
    print("\nNext step: Test the model + verifier together:")
    print(f"  python3 scripts/wake_score.py {model_path} {pos_dir} {neg_dir} --verifier {out_path}")
    print("Or test live on mic:")
    print(f"  python3 scripts/wake_live_score.py {model_path} --verifier {out_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
