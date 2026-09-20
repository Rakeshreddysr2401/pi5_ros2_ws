#!/usr/bin/env python3
"""Local Mitra wake-word model generator & trainer.

Trains an openWakeWord detection model for "Mitra" / "Hey Mitra" / "Hi Mitra"
(Sanskrit for 'Friend' — मित्र) with Indian & international TTS voices,
adversarial negatives ("meter", "mithun", "mitali", etc.), and exports
directly to `src/langrobo_ros/models/wake/mitra.onnx`.

Usage:
    python3 scripts/train_mitra_local.py
"""

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
import scipy.io.wavfile as wav
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

RATE = 16000
CHUNK = 1280
WIN_FRAMES = 16
FEAT_DIM = 96
INPUT_DIM = WIN_FRAMES * FEAT_DIM  # 1536


def get_available_voices():
    try:
        out = subprocess.check_output(["say", "-v", "?"], text=True)
    except Exception:
        return ["Rishi", "Samantha", "Daniel", "Karen", "Fred"]

    preferred = [
        "Rishi", "Aman", "Tara", "Lekha", "Geeta", "Daniel", "Samantha",
        "Karen", "Moira", "Tessa", "Fred", "Kathy", "Ralph", "Albert",
        "Eddy (English (US))", "Flo (English (US))", "Sandy (English (US))",
        "Reed (English (US))", "Grandma (English (US))", "Grandpa (English (US))"
    ]
    avail = []
    for line in out.splitlines():
        name = line.split()[0] if line else ""
        for pref in preferred:
            if pref in line and pref not in avail:
                avail.append(pref)
    return avail if avail else ["Samantha", "Daniel"]


def synthesize_task(item):
    voice, phrase, rate, out_path = item
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp_aiff:
        aiff_path = tmp_aiff.name

    try:
        subprocess.run(["say", "-v", voice, "-r", str(rate), phrase, "-o", aiff_path], check=True, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff_path, out_path], check=True, capture_output=True)
        return out_path
    except Exception:
        return None
    finally:
        if os.path.exists(aiff_path):
            os.remove(aiff_path)


def load_wav_pcm(path: str) -> np.ndarray:
    sr, data = wav.read(path)
    if data.ndim > 1:
        data = data[:, 0]
    if data.dtype != np.int16:
        data = (data * 32767).astype(np.int16)
    return data


def extract_clip_windows(feature_extractor, audio: np.ndarray):
    pad_start = np.zeros(CHUNK * 2, dtype=np.int16)
    pad_end = np.zeros(CHUNK * 5, dtype=np.int16)
    full = np.concatenate([pad_start, audio, pad_end])

    feature_extractor.reset() if hasattr(feature_extractor, "reset") else None
    for i in range(0, len(full) - CHUNK + 1, CHUNK):
        feature_extractor(full[i:i + CHUNK])

    buf = np.array(feature_extractor.feature_buffer)
    if len(buf) < WIN_FRAMES:
        return np.empty((0, WIN_FRAMES, FEAT_DIM), dtype=np.float32)

    windows = []
    for j in range(len(buf) - WIN_FRAMES + 1):
        windows.append(buf[j:j + WIN_FRAMES])
    return np.array(windows, dtype=np.float32)


def main():
    print("=" * 65)
    print("  Mitra Wake-Word Model Trainer (Local macOS)")
    print("  Target: 'Mitra' / 'Hey Mitra' / 'Hi Mitra' (Sanskrit: मित्र)")
    print("=" * 65)

    import openwakeword.utils as u
    voices = get_available_voices()
    print(f"\n1. Discovered {len(voices)} high-quality TTS voices:")
    print("   " + ", ".join(voices[:8]) + (f" ... and {len(voices)-8} more" if len(voices) > 8 else ""))

    # Sanskrit phonetics & common call forms
    positive_phrases = [
        "Mitra", "Hey Mitra", "Hi Mitra", "Hello Mitra",
        "Mithra", "Hey Mithra", "Hi Mithra",
        "Meetra", "Hey Meetra", "Hi Meetra"
    ]

    # Adversarial negatives: sound close but must NEVER trigger the robot
    negative_phrases = [
        "meter", "mithun", "mitali", "mitten", "metro", "nitro",
        "matter", "motor", "mister", "matrix", "mirror", "miller",
        "hello", "robot", "stop", "what is the time", "how are you",
        "good morning", "can you hear me", "jarvis", "alexa", "turn on the light",
        "yes", "no", "friend", "namaste"
    ]
    rates = [135, 160, 185, 210]

    work_dir = "/tmp/mitra_wake_train"
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(os.path.join(work_dir, "pos"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "neg"), exist_ok=True)

    print("\n2. Synthesizing audio variations across 8 threads...")
    t0 = time.time()

    pos_tasks = []
    count = 0
    for v in voices:
        for p in positive_phrases:
            for r in rates:
                count += 1
                dst = os.path.join(work_dir, "pos", f"pos_{count:04d}.wav")
                pos_tasks.append((v, p, r, dst))

    neg_tasks = []
    count = 0
    for v in voices:
        for p in negative_phrases:
            for r in [155, 185]:
                count += 1
                dst = os.path.join(work_dir, "neg", f"neg_{count:04d}.wav")
                neg_tasks.append((v, p, r, dst))

    all_tasks = pos_tasks + neg_tasks
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for res in executor.map(synthesize_task, all_tasks):
            if res:
                results.append(res)

    pos_files = [f for f in results if "/pos/" in f]
    neg_files = [f for f in results if "/neg/" in f]
    print(f"   Generated {len(pos_files)} positive and {len(neg_files)} negative clips in {time.time() - t0:.1f}s.")

    print("\n3. Extracting 96-dim openWakeWord embeddings...")
    t1 = time.time()
    F = u.AudioFeatures()

    X_pos_list = []
    for f in pos_files:
        pcm = load_wav_pcm(f)
        wins = extract_clip_windows(F, pcm)
        if len(wins) >= 4:
            active_idx = max(len(wins) - 6, 0)
            X_pos_list.append(wins[active_idx:active_idx + 3])

    X_neg_list = []
    for f in neg_files:
        pcm = load_wav_pcm(f)
        wins = extract_clip_windows(F, pcm)
        if len(wins) > 0:
            step = max(len(wins) // 3, 1)
            X_neg_list.append(wins[::step])

    # Background silence/noise features
    silence_pcm = np.random.normal(0, 15, CHUNK * 30).astype(np.int16)
    noise_wins = extract_clip_windows(F, silence_pcm)
    if len(noise_wins) > 0:
        X_neg_list.append(noise_wins)

    X_pos = np.vstack(X_pos_list) if X_pos_list else np.empty((0, WIN_FRAMES, FEAT_DIM))
    X_neg = np.vstack(X_neg_list) if X_neg_list else np.empty((0, WIN_FRAMES, FEAT_DIM))

    print(f"   Positive feature windows: {X_pos.shape[0]}")
    print(f"   Negative feature windows: {X_neg.shape[0]}")
    print(f"   Features extracted in {time.time() - t1:.1f}s.")

    # Create dataset
    X = np.concatenate([X_pos, X_neg], axis=0)
    y = np.concatenate([np.ones(len(X_pos), dtype=np.float32), np.zeros(len(X_neg), dtype=np.float32)])

    # Shuffle
    perm = np.random.permutation(len(X))
    X = X[perm]
    y = y[perm]

    # Split train / val
    val_size = int(len(X) * 0.15)
    X_train, X_val = X[val_size:], X[:val_size]
    y_train, y_val = y[val_size:], y[:val_size]

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train).unsqueeze(1))
    val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val).unsqueeze(1))

    train_loader = DataLoader(train_ds, batch_size=64, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=128, shuffle=False)

    print("\n4. Training PyTorch neural network...")
    # Standard openWakeWord DNN architecture
    net = nn.Sequential(
        nn.Flatten(),
        nn.Linear(INPUT_DIM, 64),
        nn.LayerNorm(64),
        nn.ReLU(),
        nn.Linear(64, 64),
        nn.LayerNorm(64),
        nn.ReLU(),
        nn.Linear(64, 1),
        nn.Sigmoid()
    )

    criterion = nn.BCELoss()
    optimizer = torch.optim.AdamW(net.parameters(), lr=1.5e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=40)

    best_val_loss = float("inf")
    best_weights = None

    epochs = 40
    for epoch in range(epochs):
        net.train()
        train_loss = 0.0
        for bx, by in train_loader:
            optimizer.zero_grad()
            pred = net(bx)
            loss = criterion(pred, by)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * len(bx)
        scheduler.step()

        net.eval()
        val_loss = 0.0
        correct = 0
        total = 0
        with torch.no_grad():
            for bx, by in val_loader:
                pred = net(bx)
                loss = criterion(pred, by)
                val_loss += loss.item() * len(bx)
                correct += ((pred >= 0.5) == (by >= 0.5)).sum().item()
                total += len(bx)

        val_acc = correct / max(total, 1)
        val_loss /= max(len(val_ds), 1)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_weights = net.state_dict()

        if (epoch + 1) % 10 == 0 or epoch == epochs - 1:
            print(f"   Epoch {epoch+1:2d}/{epochs:2d} — Val Loss: {val_loss:.4f}, Val Acc: {val_acc*100:.1f}%")

    if best_weights:
        net.load_state_dict(best_weights)

    print("\n5. Exporting trained model to ONNX...")
    out_model_path = os.path.abspath("src/langrobo_ros/models/wake/mitra.onnx")
    os.makedirs(os.path.dirname(out_model_path), exist_ok=True)

    net.eval()
    dummy = torch.randn(1, WIN_FRAMES, FEAT_DIM)
    torch.onnx.export(
        net,
        dummy,
        out_model_path,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch_size"}, "output": {0: "batch_size"}},
        opset_version=18
    )

    size_kb = os.path.getsize(out_model_path) / 1024.0
    print(f"   Successfully exported: {out_model_path} ({size_kb:.1f} KB)")

    print("\n6. Self-validating with openWakeWord...")
    from openwakeword.model import Model
    oww = Model(wakeword_model_paths=[out_model_path])
    key = list(oww.models.keys())[0]

    # Test sample positive
    test_pos = pos_files[0]
    pcm = load_wav_pcm(test_pos)
    padded = np.concatenate([np.zeros(CHUNK * 2, dtype=np.int16), pcm, np.zeros(CHUNK * 5, dtype=np.int16)])
    oww.reset()
    pos_peak = max(float(oww.predict(padded[i:i + CHUNK])[key]) for i in range(0, len(padded) - CHUNK + 1, CHUNK))

    # Test sample negative
    test_neg = neg_files[0]
    pcm = load_wav_pcm(test_neg)
    padded = np.concatenate([np.zeros(CHUNK * 2, dtype=np.int16), pcm, np.zeros(CHUNK * 5, dtype=np.int16)])
    oww.reset()
    neg_peak = max(float(oww.predict(padded[i:i + CHUNK])[key]) for i in range(0, len(padded) - CHUNK + 1, CHUNK))

    print(f"   Test Positive ({os.path.basename(test_pos)}): Peak Score = {pos_peak:.3f} (expect > 0.6)")
    print(f"   Test Negative ({os.path.basename(test_neg)}): Peak Score = {neg_peak:.3f} (expect < 0.2)")

    # Cleanup temp audio
    shutil.rmtree(work_dir, ignore_errors=True)
    print("\n" + "=" * 65)
    print("  TRAINING COMPLETE! Model ready at src/langrobo_ros/models/wake/mitra.onnx")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
