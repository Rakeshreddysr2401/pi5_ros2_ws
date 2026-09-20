# Wake Word Models (`models/wake/`)

Unlike the large TTS/STT models (Kokoro, Whisper) which are gigabytes and downloaded at runtime, **wake-word models in this directory ARE tracked in git** (see `.gitignore` lines 38-40).

## Available Models

| File | Size | Wake Word / Script | Description |
|---|---|---|---|
| `rakhi.onnx` | ~414 KB | "Rakhi" / "రాఖీ" / "Hey Rakhi" | Trained with native Telugu (`Geeta te_IN`), Indian English, and Telugu household negatives ("రాకీ", "చెప్పు", "ఆగు", etc.). |
| `mitra.onnx` | ~414 KB | "Mitra" / "Hey Mitra" | Trained with Indian & international voices for "Mitra". |
| `*_verifier.pkl` | ~50 KB | Personal verifier | *(Optional)* Personal voice verifier trained on the owner's voice clips. |

## How the Robot Uses Them

- `pi5_voice_pkg/wake/openwakeword_detector.py` loads `wake_model_path` (`rakhi.onnx` or `mitra.onnx`).
- When `wake_verifier_path` is provided, it attaches the verifier (keyed by the file stem `rakhi` or `mitra`).
- Enabled via `src/pi5_voice_pkg/config/voice_params.yaml`:
  ```yaml
  pi5_stt_node:
    ros__parameters:
      wake_detector: openwakeword
      # Choose either rakhi.onnx or mitra.onnx:
      wake_model_path: /home/rakhi24/ros2_ws/src/langrobo_ros/models/wake/rakhi.onnx
      wake_threshold: 0.5
      wake_verifier_path: ""   # or path to rakhi_verifier.pkl
      require_wake: true
  ```

See `WAKE_WORD_INTEGRATION.md` in the project root for the end-to-end recipe.
