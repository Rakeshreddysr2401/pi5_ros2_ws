# models/ — voice weights for pi5_voice_pkg (not tracked — too large for git)

Downloaded 2026-09-04. Fetch again with:

```bash
mkdir -p kokoro && cd kokoro
curl -L -o kokoro-v1.0.onnx  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o voices-v1.0.bin   https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Same source the Jetson's `ai_stack` image was built from
(`jetson-containers/packages/speech/kokoro-tts/kokoro-tts-onnx/Dockerfile`).

**Use `kokoro-v1.0.onnx` (fp32), not the int8 variant.** Measured on this Pi5
(Cortex-A76, 4 threads): fp32 RTF ~1.8, int8 RTF ~3.75 — int8 is *slower*
here. ARM NEON has no fast int8 path for this op set; ONNX Runtime's
quantized ops need x86 VNNI to win. Don't re-try int8 without re-measuring.

`whisper/` (faster-whisper `base`, int8 — this one IS faster on CPU, ctranslate2
is properly optimized for ARM) downloads itself into `whisper/` on first run
via `WhisperModel(..., download_root=...)` — no manual fetch needed.

`vad/silero_vad.onnx` — Silero VAD v5 (stt_node `vad_engine: silero`, 2.3 MB):

```bash
mkdir -p vad && curl -L -o vad/silero_vad.onnx \
  https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx
# sha256 2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f
```

`sherpa/` — local STT models for `stt_provider: sherpa` (stt_providers/local_sherpa.py),
measured in docs/voice/VOICE_LAB_2026-10.md:

```bash
mkdir -p sherpa && cd sherpa && R=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models
curl -L $R/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2 | tar xj   # best accuracy, 1 GB RAM
curl -L $R/sherpa-onnx-moonshine-base-en-int8.tar.bz2 | tar xj           # fastest at the same accuracy as tiny.en
```
