# scripts — what each one is for

They stay in this one folder on purpose: the systemd units, the Jetson's `./rover` and the
documents call them by these paths. Everything sources `ros_env.sh` (ROS 2 Lyrical + this overlay).

| group | scripts |
|---|---|
| **Run the robot** (systemd entry points: do not rename) | `run_brain.sh` `run_voice.sh` `run_microros.sh` `run_sonar.sh` (+ `sonar_node.py`), `run_stt.sh` `run_tts.sh` (one half of voice by hand) |
| **Whole fleet** | `fleet.sh` (rover / twin / check / status / stop / down), `twin_brain.sh` + `twin_chat.py` (the twin's second brain), `topic_rates.py` (the Jetson's gates call it) |
| **Set up / build** | `setup_lyrical_root.sh` (once, sudo), `build_ws.sh`, `build_xrce_agent.sh`, `install_systemd.sh`, `ros_env.sh` |
| **Studio / dev** | `start_studio.sh`, `dev.sh` |
| **Talk to it / listen** | `say.sh`, `tts_say.py`, `voice_watch.sh` |
| **LLM** | `llm_switch.sh`, `llm_cache_check.py`, `latency_replay.py`, `gemma_flow_eval.py` |
| **Audio devices** | `bt_speaker.sh` + `bt_pair.py`, `mic_check.sh`, `aec_probe.py`, `capture_ring.py` |
| **Wake word** | `wake_switch.py`, `wake_report.py`, `wake_test.sh`, `wake_live_score.py`, `wake_score.py`, `wake_record_clips.py`, `wake_train_verifier.py`, `train_kino_local.py`, `train_rakhi_local.py`, `test_mic_kino.py` |
| `voice_lab/` | STT / turn-end experiments and benches (its own README) |
| `mac_mini/` | what runs on the Mac mini: the llama.cpp build + patch, `e12b5`, the STT server |
