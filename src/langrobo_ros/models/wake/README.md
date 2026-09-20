# Wake Word Models (`models/wake/`)

Unlike the large TTS/STT models (Kokoro, Whisper) which are gigabytes and downloaded at runtime, **wake-word models in this directory ARE tracked in git** (see `.gitignore` lines 38-40).

## Expected Files

| File | Size | Description |
|---|---|---|
| `mitra.onnx` | ~300 KB – 1.2 MB | The trained openWakeWord acoustic model for "Mitra" / "Hey Mitra". |
| `mitra_verifier.pkl` | ~50 KB | *(Optional)* Personal voice verifier trained on the owner's clips. |

## How the Robot Uses Them

- `pi5_voice_pkg/wake/openwakeword_detector.py` loads `wake_model_path` (`mitra.onnx`).
- When `wake_verifier_path` is provided, it attaches `mitra_verifier.pkl` (keyed by the file stem `mitra`).
- Enabled via `src/pi5_voice_pkg/config/voice_params.yaml`:
  ```yaml
  pi5_stt_node:
    ros__parameters:
      wake_detector: openwakeword
      wake_model_path: /home/rakhi24/ros2_ws/src/langrobo_ros/models/wake/mitra.onnx
      wake_threshold: 0.5
      wake_verifier_path: /home/rakhi24/ros2_ws/src/langrobo_ros/models/wake/mitra_verifier.pkl
      require_wake: true
  ```

See `WAKE_WORD_INTEGRATION.md` in the project root for the end-to-end recipe.
