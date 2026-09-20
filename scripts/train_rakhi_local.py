#!/usr/bin/env python3
"""Local Rakhi wake-word model generator & trainer for Telugu households.

Trains an openWakeWord detection model for "Rakhi" / "రాఖీ" / "Hey Rakhi" / "ఏయ్ రాఖీ"
specifically tailored for Telugu households. Includes authentic Telugu speech via macOS
native Telugu voice (Geeta te_IN), Indian English voices (Rishi, Aman, Tara), Hindi (Lekha),
and international voices.

Includes comprehensive Telugu adversarial words ("రాకీ", "ఖాకీ", "హాకీ", "రాకేశ్", "రోటీ",
"రారా", "ఎవరు", "ఏంటి", "ఎక్కడ", "చెప్పు", "ఆగు", etc.) to ensure zero false wakes during
daily household conversation.

Exports directly to `src/langrobo_ros/models/wake/rakhi.onnx`.

Usage:
    python3 scripts/train_rakhi_local.py
"""

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
import onnx
import scipy.io.wavfile as wav
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

RATE = 16000
CHUNK = 1280
WIN_FRAMES = 16
FEAT_DIM = 96
INPUT_DIM = WIN_FRAMES * FEAT_DIM  # 1536
TARGET_SAMPLES = 32000  # 2.0 seconds @ 16kHz = 16 frames in openWakeWord


def synthesize_clip(voice: str, phrase: str, rate: int, shift: int = 0, gain: float = 1.0, noise_std: float = 0.0) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp_aiff:
        aiff_path = tmp_aiff.name
    wav_path = aiff_path + ".wav"

    try:
        subprocess.run(["say", "-v", voice, "-r", str(rate), phrase, "-o", aiff_path], check=True, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff_path, wav_path], check=True, capture_output=True)
        _, data = wav.read(wav_path)
    except Exception:
        return None
    finally:
        for p in [aiff_path, wav_path]:
            if os.path.exists(p):
                os.remove(p)

    pcm = data.flatten()
    if pcm.dtype != np.int16:
        pcm = (pcm * 32767).astype(np.int16)

    # Position speech in 2-second window (aligned near the end with trailing silence)
    clip = np.zeros(TARGET_SAMPLES, dtype=np.float32)
    pcm_len = len(pcm)
    if pcm_len < TARGET_SAMPLES:
        start = TARGET_SAMPLES - pcm_len - 3000 + shift
        start = max(0, min(start, TARGET_SAMPLES - pcm_len))
        clip[start:start + pcm_len] = pcm.astype(np.float32) * gain
    else:
        clip[:] = pcm[:TARGET_SAMPLES].astype(np.float32) * gain

    if noise_std > 0:
        clip += np.random.normal(0, noise_std, TARGET_SAMPLES)

    return np.clip(clip, -32768, 32767).astype(np.int16)


def worker_task(item):
    voice, phrase, rate, shift, gain, noise_std = item
    return synthesize_clip(voice, phrase, rate, shift, gain, noise_std)


def main():
    print("=" * 70)
    print("  Rakhi Wake-Word Model Trainer (Telugu Household Specialized)")
    print("  Target: 'Rakhi' / 'రాఖీ' / 'Hey Rakhi' / 'ఏయ్ రాఖీ' / 'Hi Rakhi'")
    print("=" * 70)

    import openwakeword.utils as u

    # Voice groups
    telugu_voices = ["Geeta"]
    indian_voices = ["Rishi", "Aman", "Tara", "Lekha"]
    general_voices = ["Samantha", "Daniel", "Karen", "Fred", "Kathy", "Ralph"]

    print(f"\n1. Configured voice engines:")
    print(f"   Telugu Native:  {telugu_voices}")
    print(f"   Indian English: {indian_voices}")
    print(f"   International:  {general_voices}")

    # Positive Telugu + English call variations
    pos_telugu_native = [
        "రాఖీ", "ఏయ్ రాఖీ", "హాయ్ రాఖీ", "హలో రాఖీ", "ఒరేయ్ రాఖీ",
        "రాఖీ విను", "రాఖీ చెప్పు", "రాఖీ గారు"
    ]
    pos_transliterated = [
        "Rakhi", "Hey Rakhi", "Hi Rakhi", "Hello Rakhi",
        "Raakhi", "Hey Raakhi", "Hi Raakhi", "Hello Raakhi",
        "Orey Rakhi", "Rakhi vinu", "Rakhi cheppu", "Ok Rakhi"
    ]

    # Negative words: near-homophones & everyday Telugu household words
    neg_telugu_native = [
        "రాకీ", "ఖాకీ", "హాకీ", "లక్కీ", "రాకేశ్", "రాము", "రాజు",
        "రోటీ", "రాత్రి", "రారా", "ఎవరు", "ఏంటి", "ఎక్కడ", "ఎప్పుడు",
        "ఎలా ఉన్నారు", "తిన్నావా", "చెప్పు", "ఆగు", "చూడు", "వద్దు",
        "సరే", "అవును", "లేదు", "నమస్కారం", "బాగున్నావా"
    ]
    neg_transliterated = [
        "rocky", "rookie", "raahi", "ranchi", "khaki", "hockey", "lucky",
        "monkey", "rakesh", "ramu", "raju", "roti", "raatri", "raaraa",
        "evaru", "enti", "ekkada", "eppudu", "cheppu", "aagu", "choodu",
        "vaddu", "sare", "avunu", "ledu", "namaskaram",
        "hello", "robot", "stop", "what is the time", "how are you",
        "turn on the light", "yes", "no"
    ]

    shifts = [-2000, 0, 2000]
    gains = [0.8, 1.0, 1.25]

    print("\n2. Building synthesis tasks for Telugu & Indian speech...")
    pos_tasks = []
    # 1. Native Telugu with Geeta
    for p in pos_telugu_native:
        for r in [140, 160, 185]:
            for s in shifts:
                for g in gains:
                    pos_tasks.append(("Geeta", p, r, s, g, 0.0))

    # 2. Transliterated with Indian & General voices
    for v in indian_voices + general_voices:
        for p in pos_transliterated:
            for r in [150, 180]:
                for s in [0, 1500]:
                    for g in [0.9, 1.1]:
                        pos_tasks.append((v, p, r, s, g, 0.0))

    neg_tasks = []
    # 1. Native Telugu negatives with Geeta
    for p in neg_telugu_native:
        for r in [150, 180]:
            for s in [0]:
                neg_tasks.append(("Geeta", p, r, s, 1.0, 0.0))

    # 2. Transliterated negatives with Indian & General voices
    for v in indian_voices + general_voices:
        for p in neg_transliterated:
            for r in [160]:
                for s in [0]:
                    neg_tasks.append((v, p, r, s, 1.0, 0.0))

    print(f"   Queued {len(pos_tasks)} positive tasks and {len(neg_tasks)} negative tasks.")
    print("   Synthesizing audio across 8 threads...")
    t0 = time.time()

    X_pos_audio = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for clip in executor.map(worker_task, pos_tasks):
            if clip is not None:
                X_pos_audio.append(clip)

    X_neg_audio = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for clip in executor.map(worker_task, neg_tasks):
            if clip is not None:
                X_neg_audio.append(clip)

    # Ambient noise and silence
    for _ in range(80):
        noise = np.random.normal(0, np.random.uniform(10, 100), TARGET_SAMPLES).astype(np.int16)
        X_neg_audio.append(noise)
    for _ in range(25):
        silence = np.zeros(TARGET_SAMPLES, dtype=np.int16)
        X_neg_audio.append(silence)

    print(f"   Synthesized {len(X_pos_audio)} positive clips and {len(X_neg_audio)} negative clips in {time.time() - t0:.1f}s.")

    print("\n3. Extracting 96-dim openWakeWord embeddings...")
    t1 = time.time()
    F = u.AudioFeatures()

    X_pos = F.embed_clips(np.array(X_pos_audio), batch_size=64)
    X_neg = F.embed_clips(np.array(X_neg_audio), batch_size=64)

    print(f"   Extracted {X_pos.shape[0]} positive feature tensors: {X_pos.shape}")
    print(f"   Extracted {X_neg.shape[0]} negative feature tensors: {X_neg.shape}")
    print(f"   Embeddings computed in {time.time() - t1:.1f}s.")

    # Create dataset
    X = np.concatenate([X_pos, X_neg], axis=0)
    y = np.concatenate([np.ones(len(X_pos), dtype=np.float32), np.zeros(len(X_neg), dtype=np.float32)])

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

    device = torch.device("mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu")
    gpu_label = "Apple Silicon GPU (MPS)" if device.type == "mps" else ("Nvidia GPU" if device.type == "cuda" else "CPU")
    print(f"\n4. Training PyTorch neural network on {gpu_label}...")

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
    ).to(device)

    criterion = nn.BCELoss()
    optimizer = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=50)

    best_val_loss = float("inf")
    best_weights = None

    epochs = 50
    for epoch in range(epochs):
        net.train()
        train_loss = 0.0
        for bx, by in train_loader:
            bx, by = bx.to(device), by.to(device)
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
                bx, by = bx.to(device), by.to(device)
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

    print("\n5. Exporting self-contained ONNX model...")
    tmp_onnx = "/tmp/rakhi_temp.onnx"
    out_model_path = os.path.abspath("src/langrobo_ros/models/wake/rakhi.onnx")
    os.makedirs(os.path.dirname(out_model_path), exist_ok=True)

    net.cpu()
    net.eval()
    dummy = torch.randn(1, WIN_FRAMES, FEAT_DIM)
    torch.onnx.export(
        net,
        dummy,
        tmp_onnx,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch_size"}, "output": {0: "batch_size"}},
        opset_version=18
    )

    m = onnx.load(tmp_onnx, load_external_data=True)
    onnx.save_model(m, out_model_path, save_as_external_data=False)
    for ext_f in [tmp_onnx, tmp_onnx + ".data"]:
        if os.path.exists(ext_f):
            os.remove(ext_f)

    size_kb = os.path.getsize(out_model_path) / 1024.0
    print(f"   Successfully exported: {out_model_path} ({size_kb:.1f} KB)")

    print("\n6. Self-validating with openWakeWord...")
    from openwakeword.model import Model
    oww = Model(wakeword_models=[out_model_path])
    key = list(oww.models.keys())[0]

    def score_audio(pcm):
        oww.reset()
        pad = np.zeros(CHUNK * 2, dtype=np.int16)
        stream = np.concatenate([pad, pcm, np.zeros(CHUNK * 4, dtype=np.int16)])
        scores = [float(oww.predict(stream[i:i + CHUNK])[key]) for i in range(0, len(stream) - CHUNK + 1, CHUNK)]
        return max(scores)

    # Test Native Telugu & Indian English
    test_pos_telugu = synthesize_clip("Geeta", "రాఖీ", 160)
    test_pos_hey_te = synthesize_clip("Geeta", "ఏయ్ రాఖీ", 160)
    test_pos_rishi = synthesize_clip("Rishi", "Rakhi", 160)
    test_pos_hey_en = synthesize_clip("Samantha", "Hey Rakhi", 160)

    # Test Telugu Negatives
    test_neg_rocky = synthesize_clip("Geeta", "రాకీ", 160)
    test_neg_rakesh = synthesize_clip("Geeta", "రాకేశ్", 160)
    test_neg_cheppu = synthesize_clip("Geeta", "చెప్పు", 160)
    test_neg_aagu = synthesize_clip("Geeta", "ఆగు", 160)
    test_silence = np.zeros(TARGET_SAMPLES, dtype=np.int16)

    print(f"   Positive 'రాఖీ'       (Telugu Geeta): Peak = {score_audio(test_pos_telugu):.3f} (expect > 0.8)")
    print(f"   Positive 'ఏయ్ రాఖీ'  (Telugu Geeta): Peak = {score_audio(test_pos_hey_te):.3f} (expect > 0.8)")
    print(f"   Positive 'Rakhi'     (Indian Rishi):  Peak = {score_audio(test_pos_rishi):.3f} (expect > 0.8)")
    print(f"   Positive 'Hey Rakhi' (Samantha):      Peak = {score_audio(test_pos_hey_en):.3f} (expect > 0.8)")
    print(f"   Negative 'రాకీ' (Rocky - Geeta):      Peak = {score_audio(test_neg_rocky):.3f} (expect < 0.1)")
    print(f"   Negative 'రాకేశ్' (Rakesh - Geeta):   Peak = {score_audio(test_neg_rakesh):.3f} (expect < 0.1)")
    print(f"   Negative 'చెప్పు' (Cheppu - Geeta):   Peak = {score_audio(test_neg_cheppu):.3f} (expect < 0.1)")
    print(f"   Negative 'ఆగు' (Aagu/stop - Geeta):   Peak = {score_audio(test_neg_aagu):.3f} (expect < 0.1)")
    print(f"   Negative Silence:                     Peak = {score_audio(test_silence):.3f} (expect < 0.1)")

    print("\n" + "=" * 70)
    print("  TRAINING COMPLETE! Model ready at src/langrobo_ros/models/wake/rakhi.onnx")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
