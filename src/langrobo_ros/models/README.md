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
