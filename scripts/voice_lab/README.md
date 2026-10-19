# voice_lab — measuring speech-to-text on the owner's voice

The record and every number: `docs/voice/VOICE_LAB_2026-10.md`. Pause the robot's
voice first (`systemctl --user stop langrobo-voice`) so only one thing listens.

| script | what it does |
|---|---|
| `record_set.py --session NAME` | record 20 sentences once, raw, to `~/voice_lab/recordings/NAME/` |
| `score_set.py [sessions]` | every STT option on those recordings → word-error table (`results.md`) |
| `stt_ab_live.py [--models parakeet] [--end 0.6] [--save]` | speak live, see local models transcribe the same untouched audio |
| `replay_test.py [--no-sarvam] [--lead S] [--trace]` | play the recordings into the REAL stt_node (domain 77) and score it end to end |
| `watch_stt.py` | watch the robot's own listening (text, provider, time) while it runs |
| `te_live.py [--sarvam] [--speak]` | Telugu → Telugu text + English (Jetson local or Sarvam), optionally spoken |
| `te_server.py`, `te_jetson_bench.py`, `te_cmd_bench.py`, `torchaudio_shim.py` | the Jetson side of local Telugu (Indic-Transcribe + IndicTrans2), parked |
| `make_clips.py`, `phrases.py`, `stt_bench.py` | the first synthetic-voice bench (ranks models; the owner's voice decides) |
