# Voice lab, 2026-10-10 — the mic, local STT, local Telugu, and Sarvam

Owner's ask: *"make speech-to-text and text-to-speech much better; try local; accuracy
first; the conference mic is good — feed it properly to Sarvam for now, local later."*
Branch `dev-1.6.3-local-voice`. This file is the record of everything measured, so the
local work can resume without redoing it. Scripts: `scripts/voice_lab/`.

**Where it stands:** the robot runs **Sarvam** (cloud) for both legs, fed by the
**AM-C28 conference mic** through a cleaned-up input path. Local English STT
(Parakeet) and local Telugu→English (Jetson) are proven on the bench, not wired in.

---

## 1. The mic — USB "Audio Array AM-C28" conference array

Plugged into the Pi 5; nothing in the repo knew about it before this.

| finding | measured | what was done |
|---|---|---|
| Input volume was **400% (+36 dB)** in PipeWire. Nothing set it (the audio owner's `wired_fallback` was `"Blackwire"`, so it never touched this device) | empty room rms 0.35 median, clipping at 4x full scale; every sound looked like speech | `audio_device_node`: new `wired_mic_gain` (1.0) applied whenever a wired mic is routed; `wired_fallback: "AM-C28"` |
| **Rumble**: 98% of the empty-room signal is below 100 Hz, peaks 2–7 Hz, swinging rms 0.02–0.52 per 100 ms (air or vibration on the mic, not sound) | webrtcvad called 30–63% of the empty room "speech"; sentences ran to the cap — the live Sarvam test waited ~15 s for a sentence to end (Sarvam itself answered in 0.3–0.4 s) Silero (next row) ignores it. A 100 Hz high-pass was tried and **removed** (owner: "don't touch my voice signal"; on the owner's recordings it made Parakeet slightly worse, 2 of 2) |
| webrtcvad cannot tell voice from room noise | 14–63% false "speech" | `vad_silero.py`: Silero VAD v5 (0% false on the same room, ~1.5 ms/frame, hysteresis 0.5 / 0.35) |
| **No speaker.** USB descriptors: one Microphone input (0x0201) and one **Headphones** output (0x0302) — a headphone/line-out socket. (An earlier "echo cancelling: ±2 dB" result was a beep played into that empty socket — it measured nothing and is withdrawn.) Echo cancelling is unknown | the **boAt Stone**'s 1 kHz beep reaches the mic at +18 dB, plus 0.5–0.8 s Bluetooth delay | the Stone is the speaker, so muting the mic while the robot talks (`tts_tail_mute_s`) must stay |
| `min_utterance_rms: 0.05` gate | tuned to the Stone's 4x-gained Bluetooth noise; the wrong yardstick here, would drop quiet speech set to 0 (off); `min_voiced_ratio` also 0 — it dropped short commands (0.22–0.31 with lead-in + tail). Silero alone decides |
| Earlier logs: one 12 s "utterance" transcribed as `'Music'` every ~12 s for days | the 400% gain + webrtcvad | fixed by the gain + Silero rows |

Devices now: **mic = AM-C28 (gain 1.0, audio untouched; Silero only marks start/end), speaker = boAt Stone
over A2DP** (`bt_prefer_mic: false`), STT and TTS = Sarvam (`stt_provider: sarvam`,
`tts_provider: sarvam_stream`). Live check after the change: room TV speech came
through clean — *"If you want to take admission, you have to pay the money to the
YouTube channel."*

### The owner's voice, measured (fan on, 2026-10-10)
One sentence, "Mitra, find the earphones on the floor and go near to it", at the
owner's usual spot, fan running:

| | |
|---|---|
| voice vs fan in the speech band (300 Hz–4 kHz) | **13–15 dB** — workable, not generous |
| voice energy 1–3 kHz / 3–8 kHz (consonants) | **4–9 % / under 1 %** (clear close speech: ~15–25 % / a few %) |
| clipping | none (peak 0.80 at hardware gain 100 %, 0.20 at 75 %); the fan's rumble bursts reached 1.00 once earlier |
| Parakeet | "Withdraw find the airports on the floor and go near to it" — vowels fine, consonants lost |

Software could not recover the consonants (same recording): louder → "Vidra finder
airfoots"; 100 Hz filter → "Withdraw finder airflows"; Wiener fan reduction → "Midra
finder airports … go get to me" (closer name, broken end); beam search → "Vidra finder
efforts". **The fix is acoustic: distance to the mic, facing it, fan not blowing at
it** — untested yet. Owner's rule from here: the mic's audio goes to the model
untouched; tune only the timing (end silence, lead-in). Live A/B with those knobs:
`scripts/voice_lab/stt_ab_live.py --end 0.6 --lead 0.3 --save`.

Owner's first live A/B (15 sentences, before the distance question): Parakeet was the
best whenever the audio was clean ("Can you find my dad and go near to him and say
hello to him?", "find the earphones on the floor and go near to it.", "Move back and
take it."), 0.25–0.54 s a sentence; all three failed together on the same few
sentences (the audio, not the model). Wake word: undecided ("Mitra" or another) —
later.

## 2. English → English, local, on the Pi 5 CPU (2 threads)

96 clips: 24 Mitra commands (`scripts/voice_lab/phrases.py`) × 4 Kokoro voices (two
Indian-accent: `hf_alpha`, `hm_omega`), mixed with 30 s of real AM-C28 room noise at
20 dB ("near") and 5 dB ("far"). Synthetic speech is easier than a real voice: this
ranks models, the owner's own voice decides.

| model (engine) | WER near / far | "Mitra" heard | s per clip | RAM |
|---|---|---|---|---|
| whisper tiny.en, beam 1 + hotword (faster-whisper) — the old local | 3.6 / 3.0 % | 24/24 | 1.2 | 340 MB |
| Moonshine tiny int8 (sherpa-onnx) | 5.7 / 7.0 % | 18–20/24 | 0.1 | 233 MB |
| Moonshine base int8 (sherpa-onnx) | 3.6 / 3.2 % | 24/24 | 0.2 | 420 MB |
| whisper base.en | 6.8 / 6.4 % | **1/24** | 2.2 | 530 MB |
| distil-whisper small.en | 2.1 / 2.1 % | 22–23/24 | 6.2 | 555 MB |
| **NVIDIA Parakeet TDT 0.6B v2 int8 (sherpa-onnx)** | **1.9 / 1.5 %** | 21–22/24 ("Mitre") | **0.4** | 1.0 GB |
| whisper small.en | 1.7 / 1.3 % | 24/24 | 7.2 | 680 MB |

**Pick: Parakeet** — half the old errors, 3x faster, Pi only. Add `mitre` to
`wake_aliases`. Moonshine base if RAM gets tight. Its slips are short single words
("Halt." → "Hold the"); "Stop." once came back empty. Moonshine on pure room noise
(x1 and x5) returned nothing — no invented sentences.

Built for it: `stt_providers/local_sherpa.py` (`stt_provider: sherpa`,
`stt_sherpa_model_dir`), Whisper stays loaded as its fallback; `sherpa-onnx==1.13.8`
(`pip3 install --user --break-system-packages sherpa-onnx==1.13.8`). Model download:
models/README.md. **Not switched on** — Sarvam is the live provider.

## 3. Telugu → English, local

Test set: 30 real Telugu speakers from Google FLEURS `te_in` test (news sentences,
10–15 s), scored against FLEURS's own Telugu transcript and the parallel English
sentence (chrF, 0–100).

| route | where | Telugu WER | English chrF | time |
|---|---|---|---|---|
| Whisper small `task=translate` | Pi CPU | — | useless ("These leaves can be used to remove the leaves from the plant" for "These couples may choose to make an adoption plan") | ~7 s |
| **Indic-Transcribe-core** (bodhan-ai, 1.2B, Canary) → **IndicTrans2 indic-en dist-200M** | **Jetson GPU** (bf16 / fp16) | **5.9 %** | **69.5** (71.0 on the perfect Telugu: ASR barely costs) | 3.8 s + 2.2 s per long clip |

Short commands (the robot's case), Jetson GPU: speech→Telugu **0.8 s (2 s clip),
1.2 s (3 s), 1.9 s (5 s)**; Telugu→English **0.46 s greedy / 0.56 s beam 5**. All 13
hand-written Telugu commands translated correctly ("ఎడమ వైపు తిరుగు" → "Turn left",
"షాపింగ్ లిస్ట్‌లో పాలు చేర్చు" → "add milk to shopping list"). **"మిత్ర" becomes
"Friend"** — stt_node already takes "Friend," at the start (`wake_leading_aliases`);
better is to look for మిత్ర in the Telugu text before translating.

Published: Indic-Transcribe-core Telugu 11.4 % vs Sarvam Saaras V3 13.5 % (Voice of
India benchmark, model card).

**Where it can run:** 2.9 GB of GPU memory. Fine with the rover parked (6.3 GB free);
**not while driving** (nvblox + Nav2 + cuVSLAM leave ~1.6 GB). The Pi 5 cannot hold
it: loading the 4.9 GB fp32 checkpoint for an int8 test on the Pi (3.8 GB free) most
likely froze it — the owner had to power-cycle (2026-10-10 ~12:05). Do not retry that
on the Pi; if a Pi build is ever wanted, quantise on the Jetson first.

Not usable here: Bodhan **Indic-Translate** (7.9B) and **Indic-Speak** Telugu TTS
(3.8B + vocoder) — too large for either board.

### Gotchas met on the way
- All four models are gated: accept each license on the HF page **while logged in as
  the account the token belongs to** (`rakhi24`); a 403 body "you are not in the
  authorized list" means the acceptance is not on that account. Token: `~/.cache/huggingface/token` (Pi), passed to the Jetson over ssh stdin, never written there.
- IndicTrans2 needs `IndicTransToolkit.IndicProcessor` (Telugu → Devanagari script +
  tags). Without it the output is Hindi-script garbage (chrF 6).
- IndicTrans2's model code breaks with transformers 4.57's KV cache: `use_cache=False`.
- orin-nav:1.1's torchaudio is built for CUDA 13.2, torch for 13.0 → import fails.
  Indic-Transcribe uses torchaudio only to resample non-16 kHz audio:
  `scripts/voice_lab/torchaudio_shim.py` stands in (raises if resampling is needed).
- Libraries live in `~/voice_lab/pylib` on the Jetson host (pip `--target`, `--no-deps`,
  pinned: transformers 4.57.1, tokenizers 0.22.1, huggingface_hub 0.36.0, plus
  IndicTransToolkit, indic-nlp-library, sacremoses, joblib, cloudpickle, …). The image
  is untouched.

### Re-run the live Telugu test
```bash
# Jetson (parked only): models in ~/voice_lab/models, ~6.6 GB on disk
ssh rakhi24@rakhi-jetson.local 'docker run -d --name te_lab --entrypoint /bin/bash --runtime=nvidia --network=host \
  -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=all -e NVIDIA_DISABLE_REQUIRE=1 --user $(id -u):$(id -g) \
  -e HOME=/lab -e PYTHONPATH=/lab/shim:/lab/pylib -e HF_HUB_OFFLINE=1 -v ~/voice_lab:/lab orin-nav:1.1 -c "python3 -u /lab/te_server.py"'
# Pi: speak Telugu at the AM-C28 (pause langrobo-voice first, or it answers too)
python3 scripts/voice_lab/te_live.py            # local (Jetson)
python3 scripts/voice_lab/te_live.py --sarvam   # Sarvam: Telugu text + English, ~0.3-0.4 s a call
# afterwards: ssh rakhi24@rakhi-jetson.local 'docker rm -f te_lab'
```

## 4. Sarvam, measured on this link
`saaras:v3` REST, 3 s of audio: 0.75 s first call (connection), then **0.29–0.40 s**.
Transcribe and translate can run in parallel. On a pure tone Sarvam returned nothing;
Indic-Transcribe answered "ఓకే" — the local model guesses words from non-speech, so
the input gate matters more for it.

## 5. TTS — not measured yet
Candidates downloaded then lost in the reboot (they were in /tmp): Piper
`lessac-high`, `ryan-high`, `hfc_female-medium`, `amy-medium`, `en_GB-alba`,
`en_GB-cori-high`, `libritts_r`; Kitten TTS nano/mini (sherpa-onnx). Known: Piper
lessac-medium RTF ~0.25, Kokoro RTF ~2 on this Pi (PI5_VOICE.md).

## 6. Open decisions / next
1. **Talking over the robot (barge-in)**: the AM-C28 has no speaker, and the Stone's
   sound reaches the mic at +18 dB, so the mic stays muted while the robot talks.
   Hearing the owner mid-reply would need echo cancelling in software (PipeWire's
   echo-cancel module, with the Stone as its reference) — untested.
2. Local English: switch to Parakeet (`stt_provider: sherpa`), owner tests live.
3. Local Telugu: a parked-only service on the Jetson, handing over to English
   Parakeet or Sarvam while driving.
4. The Mac mini was unreachable after the 2026-10-10 power cycle (name did not
   resolve) — the brain and the "is it for me?" check need it.
