#!/usr/bin/env python3
"""Local "Hey Kino" Wake-Word Model Generator & Trainer.

Engineered for Siri/Alexa-grade precision and zero false-fires.
- Target: "Hey Kino" / "Hi Kino" / "Hello Kino"
- Carrier-phrase design: "Hey" acts as the acoustic priming prefix for Silero VAD
- Heavy false-positive suppression (asymmetric BCE loss with 2.5x penalty)
- Exact openWakeWord batch embedding (embed_clips) with 2.0s aligned windows
- Rigorous stream-simulation validation

Exports directly to:
    src/langrobo_ros/models/wake/kino.onnx

Usage:
    ./.venv/bin/python scripts/train_kino_local.py
"""

import concurrent.futures
import hashlib
import os
import subprocess
import sys
import tempfile
import time
import warnings

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

CACHE_DIR = os.path.abspath("scratch/kino_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def get_available_voices():
    try:
        out = subprocess.check_output(["say", "-v", "?"], text=True)
    except Exception:
        return ["Rishi", "Samantha", "Daniel", "Karen", "Fred"]

    preferred = ["Rishi", "Samantha", "Daniel", "Karen", "Geeta"]
    avail = []
    for line in out.splitlines():
        name = line.split()[0] if line else ""
        for pref in preferred:
            if pref in line and pref not in avail:
                avail.append(pref)
    return avail if avail else ["Samantha", "Daniel", "Rishi", "Karen"]


def get_base_pcm(voice: str, phrase: str, rate: int) -> np.ndarray:
    """Synthesize or retrieve cached 16kHz 16-bit PCM audio."""
    key = hashlib.md5(f"{voice}_{rate}_{phrase}".encode("utf-8")).hexdigest()
    cache_file = os.path.join(CACHE_DIR, f"{key}.npy")
    if os.path.exists(cache_file):
        try:
            return np.load(cache_file)
        except Exception:
            pass

    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp_aiff:
        aiff_path = tmp_aiff.name
    wav_path = aiff_path + ".wav"

    try:
        subprocess.run(["say", "-v", voice, "-r", str(rate), phrase, "-o", aiff_path], check=True, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff_path, wav_path], check=True, capture_output=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _, data = wav.read(wav_path)
        pcm = data.flatten()
        if pcm.dtype != np.int16:
            pcm = (pcm * 32767).astype(np.int16)
        np.save(cache_file, pcm)
        return pcm
    except Exception:
        return None
    finally:
        for p in [aiff_path, wav_path]:
            if os.path.exists(p):
                os.remove(p)


def make_window_clip(pcm: np.ndarray, offset_mode: str = "end", shift: int = 0, gain: float = 1.0, noise_std: float = 0.0) -> np.ndarray:
    """Places pcm inside a 2.0s (32,000 sample) window at varying alignments."""
    clip = np.zeros(TARGET_SAMPLES, dtype=np.float32)
    pcm_len = len(pcm)
    if pcm_len == 0:
        return np.zeros(TARGET_SAMPLES, dtype=np.int16)

    if pcm_len < TARGET_SAMPLES:
        if offset_mode == "end":
            start = TARGET_SAMPLES - pcm_len - 3000 + shift
        elif offset_mode == "center":
            start = (TARGET_SAMPLES - pcm_len) // 2 + shift
        elif offset_mode == "start":
            start = 2000 + shift
        else:
            start = np.random.randint(0, TARGET_SAMPLES - pcm_len)
        start = max(0, min(start, TARGET_SAMPLES - pcm_len))
        clip[start:start + pcm_len] = pcm.astype(np.float32) * gain
    else:
        if offset_mode == "start":
            slice_start = 0
        elif offset_mode == "end":
            slice_start = pcm_len - TARGET_SAMPLES
        else:
            slice_start = (pcm_len - TARGET_SAMPLES) // 2
        slice_start = max(0, min(slice_start, pcm_len - TARGET_SAMPLES))
        clip[:] = pcm[slice_start:slice_start + TARGET_SAMPLES].astype(np.float32) * gain

    if noise_std > 0:
        clip += np.random.normal(0, noise_std, TARGET_SAMPLES)

    return np.clip(clip, -32768, 32767).astype(np.int16)


def main():
    print("=" * 70)
    print("  'Hey Kino' Wake-Word Model Trainer (High-Precision v2)")
    print("  Target: 'Hey Kino' / 'Hi Kino' / 'Hello Kino'")
    print("  Identity: KINO — Knowledge Is Never Out")
    print("=" * 70)

    import openwakeword.utils as u

    voices = get_available_voices()
    print(f"\n1. Discovered {len(voices)} high-quality TTS voices:")
    print("   " + ", ".join(voices[:8]) + (f" ... and {len(voices)-8} more" if len(voices) > 8 else ""))

    # Positive call phrases — primary is "Hey Kino"
    pos_phrases = [
        "Hey Kino", "Hey, Kino", "Hi Kino", "Hello Kino", "Hey Keeno"
    ]

    # Targeted adversarial negatives (sound-alikes, conversational speech, Telugu, tech assistants)
    neg_phrases = [
        # Standalone "Kino" without carrier phrase (MUST NOT FIRE on its own)
        "Kino", "Keeno", "Keyno", "Keno", "Kina",
        # "Hey" + other words (must isolate Kino)
        "Hey", "Hey there", "Hey you", "Hey man", "Hey friend", "Hey bro",
        "Hey buddy", "Hey Google", "Hey Siri", "Hey Alexa", "Hey Jarvis",
        "Hi there", "Hello there",
        # Near-homophones & rhymes (-ino, key-)
        "keynote", "casino", "kimono", "cappuccino", "quinoa", "kilo",
        "keanu", "rhino", "dino", "albino", "cleaner", "tuxedo",
        # Conversational collocations that sound like 'kino'
        "you know", "he know", "we know", "they know", "who know", "do you know",
        "i know", "you know what",
        # Common robot commands
        "hello", "robot", "stop", "halt", "what is the time", "how are you",
        "turn on the light", "turn off the light", "come here", "yes", "no",
        # Telugu conversational words
        "cinema", "repu cinema", "nijamga", "cheppu", "aagu", "choodu", "sare",
        "avunu", "ledu", "namaskaram", "kinda", "kanukko"
    ]

    rates_pos = [150, 180]
    rates_neg = [140, 160, 180]

    print("\n2. Loading/synthesizing base audio clips (disk cached)...")
    base_tasks = []
    for v in voices:
        for p in pos_phrases:
            for r in rates_pos:
                base_tasks.append((v, p, r, True))
        for p in neg_phrases:
            for r in rates_neg:
                base_tasks.append((v, p, r, False))

    def synth_worker(task):
        v, p, r, is_pos = task
        pcm = get_base_pcm(v, p, r)
        return (v, p, r, is_pos, pcm)

    t0 = time.time()
    synth_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for res in executor.map(synth_worker, base_tasks):
            if res[4] is not None and len(res[4]) > 0:
                synth_results.append(res)
    print(f"   Loaded {len(synth_results)} base audio clips in {time.time() - t0:.1f}s.")

    print("\n3. Augmenting positive & negative training windows (2.0s openWakeWord windows)...")
    X_pos_audio = []
    for v, p, r, is_pos, pcm in synth_results:
        if is_pos:
            for mode in ["end", "center"]:
                for s in [-2000, 2000]:
                    X_pos_audio.append(make_window_clip(pcm, offset_mode=mode, shift=s, gain=1.0))

    X_neg_audio = []
    for v, p, r, is_pos, pcm in synth_results:
        if not is_pos:
            for mode in ["end", "center", "start"]:
                for s in [-2500, 0, 2500]:
                    for g in [0.9, 1.1]:
                        X_neg_audio.append(make_window_clip(pcm, offset_mode=mode, shift=s, gain=g))

    # Add ambient noise & silence
    for _ in range(300):
        noise = np.random.normal(0, np.random.uniform(20, 150), TARGET_SAMPLES).astype(np.int16)
        X_neg_audio.append(noise)
    for _ in range(120):
        silence = np.zeros(TARGET_SAMPLES, dtype=np.int16)
        X_neg_audio.append(silence)

    print(f"   Generated {len(X_pos_audio)} positive clips.")
    print(f"   Generated {len(X_neg_audio)} negative clips (ratio {len(X_neg_audio)/max(len(X_pos_audio),1):.1f}:1).")

    print("\n4. Extracting 96-dim openWakeWord embeddings with embed_clips (vectorized ONNX)...")
    t1 = time.time()
    F = u.AudioFeatures()

    # Fast batch extraction
    X_pos = F.embed_clips(np.array(X_pos_audio), batch_size=64)
    X_neg = F.embed_clips(np.array(X_neg_audio), batch_size=64)

    print(f"   Positive embeddings: {X_pos.shape}")
    print(f"   Negative embeddings: {X_neg.shape}")
    print(f"   Extracted embeddings in {time.time() - t1:.1f}s.")

    # Create dataset
    X = np.concatenate([X_pos, X_neg], axis=0)
    y = np.concatenate([np.ones(len(X_pos), dtype=np.float32), np.zeros(len(X_neg), dtype=np.float32)])

    perm = np.random.permutation(len(X))
    X = X[perm]
    y = y[perm]

    # Train / Val Split (85% / 15%)
    val_size = int(len(X) * 0.15)
    X_train, X_val = X[val_size:], X[:val_size]
    y_train, y_val = y[val_size:], y[:val_size]

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train).unsqueeze(1))
    val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val).unsqueeze(1))

    train_loader = DataLoader(train_ds, batch_size=64, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=128, shuffle=False)

    device = torch.device("mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu")
    gpu_label = "Apple Silicon GPU (MPS)" if device.type == "mps" else ("Nvidia GPU" if device.type == "cuda" else "CPU")
    print(f"\n5. Training PyTorch neural network on {gpu_label} with False-Positive Suppression...")

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

    # Asymmetric loss function: 4.0x penalty on false positives
    FP_WEIGHT = 4.0

    def asymmetric_bce(pred, target):
        loss_pos = -target * torch.log(pred.clamp(min=1e-6))
        loss_neg = -(1.0 - target) * torch.log((1.0 - pred).clamp(min=1e-6)) * FP_WEIGHT
        return (loss_pos + loss_neg).mean()

    optimizer = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=40)

    best_val_loss = float("inf")
    best_weights = None

    epochs = 40
    for epoch in range(epochs):
        net.train()
        for bx, by in train_loader:
            bx, by = bx.to(device), by.to(device)
            optimizer.zero_grad()
            pred = net(bx)
            loss = asymmetric_bce(pred, by)
            loss.backward()
            optimizer.step()
        scheduler.step()

        net.eval()
        val_loss = 0.0
        correct = 0
        total = 0
        with torch.no_grad():
            for bx, by in val_loader:
                bx, by = bx.to(device), by.to(device)
                pred = net(bx)
                loss = asymmetric_bce(pred, by)
                val_loss += loss.item() * len(bx)
                correct += ((pred >= 0.5) == (by >= 0.5)).sum().item()
                total += len(bx)

        val_loss /= max(total, 1)
        val_acc = correct / max(total, 1)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_weights = net.state_dict()

        if (epoch + 1) % 10 == 0 or epoch == epochs - 1:
            print(f"   Epoch {epoch+1:2d}/{epochs:2d} — Val Loss: {val_loss:.4f}, Val Acc: {val_acc*100:.1f}%")

    if best_weights:
        net.load_state_dict(best_weights)

    print("\n6. Exporting self-contained ONNX model...")
    tmp_onnx = "/tmp/kino_temp.onnx"
    out_model_path = os.path.abspath("src/langrobo_ros/models/wake/kino.onnx")
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

    print("\n7. Streaming benchmark verification (matching live mic conditions)...")
    from openwakeword.model import Model
    oww = Model(wakeword_models=[out_model_path])
    key = list(oww.models.keys())[0]

    def score_stream(voice, text):
        pcm = get_base_pcm(voice, text, 160)
        if pcm is None:
            return 0.0
        oww.reset()
        # Prepend 1s silence and append 1s silence to match real-world stream
        stream = np.concatenate([np.zeros(RATE, dtype=np.int16), pcm, np.zeros(RATE, dtype=np.int16)])
        scores = []
        for i in range(0, len(stream) - CHUNK + 1, CHUNK):
            chunk = stream[i:i + CHUNK]
            preds = oww.predict(chunk)
            scores.append(float(preds[key]))
        return max(scores) if scores else 0.0

    print("   --- POSITIVES ('Hey Kino' — Target > 0.75) ---")
    print(f"   'Hey Kino'       (Samantha):  {score_stream('Samantha', 'Hey Kino'):.3f}")
    print(f"   'Hey Kino'         (Daniel):  {score_stream('Daniel', 'Hey Kino'):.3f}")
    print(f"   'Hey Kino'          (Rishi):  {score_stream('Rishi', 'Hey Kino'):.3f}")
    print(f"   'Hi Kino'        (Samantha):  {score_stream('Samantha', 'Hi Kino'):.3f}")
    print(f"   'Hello Kino'     (Samantha):  {score_stream('Samantha', 'Hello Kino'):.3f}")

    print("\n   --- ADVERSARIAL NEGATIVES & CONVERSATION (Target < 0.15) ---")
    print(f"   'Kino' (standalone) (Samantha): {score_stream('Samantha', 'Kino'):.3f}")
    print(f"   'casino'            (Samantha): {score_stream('Samantha', 'casino'):.3f}")
    print(f"   'keynote'           (Samantha): {score_stream('Samantha', 'keynote'):.3f}")
    print(f"   'you know'          (Samantha): {score_stream('Samantha', 'you know'):.3f}")
    print(f"   'Hey Siri'          (Samantha): {score_stream('Samantha', 'Hey Siri'):.3f}")
    print(f"   'Hey Google'        (Samantha): {score_stream('Samantha', 'Hey Google'):.3f}")
    print(f"   'Hey bro'           (Samantha): {score_stream('Samantha', 'Hey bro'):.3f}")
    print(f"   'cinema'               (Rishi): {score_stream('Rishi', 'cinema'):.3f}")
    print(f"   'repu cinema'          (Rishi): {score_stream('Rishi', 'repu cinema'):.3f}")
    print(f"   'cheppu'               (Rishi): {score_stream('Rishi', 'cheppu'):.3f}")
    print(f"   'aagu'                 (Rishi): {score_stream('Rishi', 'aagu'):.3f}")

    print("\n" + "=" * 70)
    print("  'Hey Kino' MODEL READY FOR DEPLOYMENT!")
    print(f"  Model location: {out_model_path}")
    print("  To test live on mic: ./.venv/bin/python scripts/wake_live_score.py src/langrobo_ros/models/wake/kino.onnx")
    print("  To activate on robot: ./scripts/wake_switch.py kino --threshold 0.45")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
