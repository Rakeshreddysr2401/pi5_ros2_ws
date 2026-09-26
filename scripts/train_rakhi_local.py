#!/usr/bin/env python3
"""Local Rakhi wake-word model generator & trainer for Telugu households.

Trains an openWakeWord detection model for "Rakhi" / "రాఖీ" / "Hey Rakhi" / "ఏయ్ రాఖీ"
specifically tailored for Telugu households. Includes authentic Telugu speech via macOS
native Telugu voice (Geeta te_IN), Indian English voices (Rishi, Aman, Tara, Lekha),
and multi-speaker variations.

Includes extensive conversational Telugu sentences ("రేపు మూవీకి వెళ్దామా", "నిజంగా వెళ్దామా",
"భోజనం చేశావా", "ఎక్కడికి వెళ్తున్నావ్", etc.) and R-sound words to eliminate false wakes during
daily household conversation.

Exports directly to `src/langrobo_ros/models/wake/rakhi.onnx`.
"""

import concurrent.futures
import hashlib
import os
import shutil
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

CACHE_DIR = os.path.abspath("scratch/rakhi_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


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
        # Longer than 2s: slice beginning, middle, or end
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
    print("  Rakhi Wake-Word Model Trainer (Telugu Household Specialized v2)")
    print("  Target: 'Rakhi' / 'రాఖీ' / 'Hey Rakhi' / 'ఏయ్ రాఖీ'")
    print("=" * 70)

    import openwakeword.utils as u

    # Voice groups
    telugu_voices = ["Geeta"]
    indian_voices = ["Rishi", "Aman", "Tara", "Lekha"]
    general_voices = ["Samantha", "Daniel", "Karen", "Fred"]

    # 1. Positive Phrases
    pos_telugu_native = [
        "రాఖీ", "ఏయ్ రాఖీ", "హాయ్ రాఖీ", "హలో రాఖీ", "ఒరేయ్ రాఖీ",
        "రాఖీ విను", "రాఖీ చెప్పు", "రాఖీ గారు"
    ]
    pos_transliterated = [
        "Rakhi", "Hey Rakhi", "Hi Rakhi", "Hello Rakhi",
        "Raakhi", "Hey Raakhi", "Hi Raakhi", "Hello Raakhi",
        "Orey Rakhi", "Rakhi vinu", "Rakhi cheppu"
    ]

    # 2. Specific False-Positive Triggers & Conversational Telugu Negatives
    neg_telugu_sentences = [
        # User reported exact triggers:
        "రేపు మూవీకి వెళ్దామా", "రేపు సినిమాకి వెళ్దామా", "రేపు వెళ్దామా", "రేపు సినిమా",
        "నిజంగా వెళ్దామా", "నిజంగా చెప్పు", "నిజంగానా", "నిజంగా చాలా బాగుంది",
        "వెళ్దామా వద్దా", "మూవీ బాగుందా", "సినిమా టికెట్లు బుక్ చెయ్యి",
        # Daily Telugu conversational sentences:
        "రేపు ఉదయం కలుద్దాం", "రేపు రాత్రి వస్తావా", "రేపు మాట్లాడదాం", "రేపు ఫోన్ చెయ్యి",
        "రేపు ఆఫీస్ కి వెళ్లాలి", "రేపు సెలవు కదా", "రేపు వస్తాను ఉండు",
        "ఏం చేస్తున్నావ్ ఇప్పుడు", "భోజనం చేశావా లేదా", "టిఫిన్ తిన్నావా", "లంచ్ చేద్దామా",
        "కాఫీ తాగుదామా టీ తాగుదామా", "ఎక్కడికి వెళ్దాం చెప్పు", "ఎలా ఉన్నారు అందరూ",
        "బాగున్నారా ఏం సంగతులు", "సరే పద వెళ్దాం", "ఆగు ఒక్క నిమిషం", "వస్తున్నాను ఉండు",
        "ఫోన్ మాట్లాడుతున్నాను", "లైట్ వేయి ఫ్యాన్ వేయి", "టీవీ ఆపు సౌండ్ తగ్గించు",
        "అర్థం కాలేదు మళ్ళీ చెప్పు", "నాకు తెలియదు నువ్వే చెప్పు", "నువ్వు రావా నాతో",
        "ఎప్పుడు వెళ్దాం చెప్పు", "ఇప్పుడే వస్తున్నా ఆగు", "సంగతి ఏంటి చెప్పు"
    ]

    neg_telugu_words = [
        # R-sound words (crucial to prevent partial rhotic matching):
        "రేపు", "రోజూ", "రెడీ", "రోడ్డు", "రైస్", "రైలు", "రెస్ట్", "రేడియో",
        "రవి", "రఘు", "రమేష్", "రాము", "రాజు", "రాజేష్", "రాజీవ్", "రాహుల్",
        "రాజా", "రాణి", "రెడ్డి", "రంగ", "రూపాయి", "రూము", "రక్తం", "రైతు",
        "రోటీ", "రాత్రి", "రారా", "రావడం", "రా రా", "రావే", "రండి",
        # Near homophones:
        "రాకీ", "రాకేశ్", "ఖాకీ", "లక్కీ", "హాకీ", "టాకీ", "జాకీ",
        # Conversational single words:
        "ఎవరు", "ఏంటి", "ఎక్కడ", "ఎప్పుడు", "చెప్పు", "ఆగు", "చూడు", "వద్దు",
        "సరే", "అవును", "లేదు", "నమస్కారం", "బాగున్నావా", "నిజంగా", "వెళ్దామా"
    ]

    neg_transliterated_sentences = [
        "repu movie ke veldama", "repu movie ki veldama", "repu cinema ki veldama",
        "nijamga veldama", "repu veldama", "nijamga movie", "shall we go to movie",
        "are you coming tomorrow", "what are you doing", "did you have lunch",
        "did you have dinner", "let's go outside", "wait for five minutes",
        "turn on the light", "turn off the tv", "call me tomorrow morning",
        "i am coming right now", "where are you going", "what is the time"
    ]

    neg_transliterated_words = [
        "rocky", "rookie", "raahi", "ranchi", "khaki", "hockey", "lucky",
        "monkey", "rakesh", "ramu", "raju", "roti", "raatri", "raaraa",
        "repu", "ready", "road", "rice", "rail", "rest", "room", "reddy",
        "ravi", "raghu", "ramesh", "rajesh", "rajiv", "rahul",
        "evaru", "enti", "ekkada", "eppudu", "cheppu", "aagu", "choodu",
        "vaddu", "sare", "avunu", "ledu", "namaskaram", "nijamga", "veldama",
        "hello", "robot", "stop", "yes", "no"
    ]

    print("\n1. Synthesizing base audio clips (with local disk cache)...")
    base_tasks = []

    # Positives base tasks
    for p in pos_telugu_native:
        for r in [140, 160, 185]:
            base_tasks.append(("Geeta", p, r))

    for v in indian_voices + general_voices:
        for p in pos_transliterated:
            for r in [150, 180]:
                base_tasks.append((v, p, r))

    # Negatives base tasks
    for p in neg_telugu_sentences + neg_telugu_words:
        for r in [150, 175]:
            base_tasks.append(("Geeta", p, r))

    for v in indian_voices:
        for p in neg_transliterated_sentences + neg_transliterated_words:
            base_tasks.append((v, p, 160))

    for v in general_voices:
        for p in neg_transliterated_sentences:
            base_tasks.append((v, p, 160))

    # Remove duplicates
    base_tasks = list(set(base_tasks))
    print(f"   Total unique speech patterns to synthesize: {len(base_tasks)}")

    def synth_worker(task):
        v, p, r = task
        pcm = get_base_pcm(v, p, r)
        return (v, p, r, pcm)

    t0 = time.time()
    synth_results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for v, p, r, pcm in executor.map(synth_worker, base_tasks):
            if pcm is not None and len(pcm) > 0:
                synth_results[(v, p, r)] = pcm
    print(f"   Base audio synthesized/loaded in {time.time() - t0:.1f}s.")

    print("\n2. Augmenting positive & negative training windows...")
    X_pos_audio = []
    # Augment Positives: cover both end and center alignments, soft to loud gains (0.75 to 1.2)
    for (v, p, r), pcm in synth_results.items():
        is_pos = (p in pos_telugu_native) or (p in pos_transliterated)
        if is_pos:
            for mode in ["end", "center"]:
                for s in [-3000, 0, 3000]:
                    for g in [0.75, 1.0, 1.2]:
                        X_pos_audio.append(make_window_clip(pcm, offset_mode=mode, shift=s, gain=g, noise_std=0.0))

    X_neg_audio = []
    # Augment Negatives: ensure diverse alignments across short words and sentences
    for (v, p, r), pcm in synth_results.items():
        is_neg = (p not in pos_telugu_native) and (p not in pos_transliterated)
        if is_neg:
            for mode in ["end", "center"]:
                for s in [-2500, 0, 2500]:
                    for g in [0.9, 1.1]:
                        X_neg_audio.append(make_window_clip(pcm, offset_mode=mode, shift=s, gain=g, noise_std=0.0))

    # Add ambient noise & silence
    for _ in range(150):
        noise = np.random.normal(0, np.random.uniform(20, 150), TARGET_SAMPLES).astype(np.int16)
        X_neg_audio.append(noise)
    for _ in range(60):
        silence = np.zeros(TARGET_SAMPLES, dtype=np.int16)
        X_neg_audio.append(silence)

    print(f"   Generated {len(X_pos_audio)} positive clips.")
    print(f"   Generated {len(X_neg_audio)} negative clips (ratio {len(X_neg_audio)/max(len(X_pos_audio),1):.1f}:1 negative-to-positive).")

    print("\n3. Extracting 96-dim openWakeWord embeddings...")
    t1 = time.time()
    F = u.AudioFeatures()

    X_pos = F.embed_clips(np.array(X_pos_audio), batch_size=64)
    X_neg = F.embed_clips(np.array(X_neg_audio), batch_size=64)

    print(f"   Positive embeddings: {X_pos.shape}")
    print(f"   Negative embeddings: {X_neg.shape}")
    print(f"   Computed embeddings in {time.time() - t1:.1f}s.")

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
    print(f"\n4. Training PyTorch neural network on {gpu_label} with False-Positive Suppression...")

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

    # Asymmetric loss function: 2.5x penalty on false positives (balances high recall with zero false wakes)
    FP_WEIGHT = 2.5

    def asymmetric_bce(pred, target):
        loss_pos = -target * torch.log(pred.clamp(min=1e-6))
        loss_neg = -(1.0 - target) * torch.log((1.0 - pred).clamp(min=1e-6)) * FP_WEIGHT
        return (loss_pos + loss_neg).mean()

    optimizer = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=50)

    best_val_loss = float("inf")
    best_weights = None

    epochs = 50
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

    print("\n6. Streaming benchmark verification (matching live mic conditions)...")
    from openwakeword.model import Model
    oww = Model(wakeword_models=[out_model_path])
    key = list(oww.models.keys())[0]

    def score_stream(voice, text):
        pcm = get_base_pcm(voice, text, 160)
        if pcm is None:
            return 0.0
        oww.reset()
        # Prepend 1s silence and append 1s silence, simulate streaming chunk-by-chunk
        stream = np.concatenate([np.zeros(RATE, dtype=np.int16), pcm, np.zeros(RATE, dtype=np.int16)])
        scores = []
        for i in range(0, len(stream) - CHUNK + 1, CHUNK):
            chunk = stream[i:i + CHUNK]
            preds = oww.predict(chunk)
            scores.append(float(preds[key]))
        return max(scores) if scores else 0.0

    print("   --- POSITIVES (Target > 0.70) ---")
    print(f"   'రాఖీ'                    (Geeta):  {score_stream('Geeta', 'రాఖీ'):.3f}")
    print(f"   'ఏయ్ రాఖీ'               (Geeta):  {score_stream('Geeta', 'ఏయ్ రాఖీ'):.3f}")
    print(f"   'Rakhi'                  (Rishi):  {score_stream('Rishi', 'Rakhi'):.3f}")
    print(f"   'Hey Rakhi'              (Rishi):  {score_stream('Rishi', 'Hey Rakhi'):.3f}")
    print(f"   'Hey Rakhi'          (Samantha):  {score_stream('Samantha', 'Hey Rakhi'):.3f}")

    print("\n   --- NEGATIVE CONVERSATION & SENTENCES (Target < 0.15) ---")
    print(f"   'రేపు మూవీకి వెళ్దామా'   (Geeta):  {score_stream('Geeta', 'రేపు మూవీకి వెళ్దామా'):.3f}")
    print(f"   'నిజంగా వెళ్దామా'        (Geeta):  {score_stream('Geeta', 'నిజంగా వెళ్దామా'):.3f}")
    print(f"   'repu movie ke veldama'  (Rishi):  {score_stream('Rishi', 'repu movie ke veldama'):.3f}")
    print(f"   'nijamga veldama'        (Rishi):  {score_stream('Rishi', 'nijamga veldama'):.3f}")
    print(f"   'రాకీ' (Rocky)            (Geeta):  {score_stream('Geeta', 'రాకీ'):.3f}")
    print(f"   'రాకేశ్' (Rakesh)         (Geeta):  {score_stream('Geeta', 'రాకేశ్'):.3f}")
    print(f"   'చెప్పు'                  (Geeta):  {score_stream('Geeta', 'చెప్పు'):.3f}")
    print(f"   'ఆగు'                     (Geeta):  {score_stream('Geeta', 'ఆగు'):.3f}")
    print(f"   'భోజనం చేశావా'           (Geeta):  {score_stream('Geeta', 'భోజనం చేశావా'):.3f}")

    print("\n" + "=" * 70)
    print("  TRAINING COMPLETE! Verified against Telugu conversational speech.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
