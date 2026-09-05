# Voice quality — getting STT/TTS closer to "Siri good"

Why the robot mishears, what actually moves the needle, and how to train the
custom "hey chotu" wake-word model. Written 2026-07-06 against the current
stack: Whisper `small` (CUDA) + SileroVAD (onnx) + openWakeWord
(`hey_jarvis_v0.1`) + transcript gate on the Jetson (`~/robot`,
voice_pkg/voice_params.yaml), Kokoro TTS → `ec_speaker` (PipeWire AEC).

## The honest framing first

Siri feels magic because of three things, in this order: a **beamforming
far-field mic array**, **server-scale models**, and aggressive
**end-pointing** tuned by thousands of engineers. You can close most of the
gap locally — but the order matters: **microphone first, model second,
parameters third.** No model fixes bad audio in.

## 0. UPDATE 2026-09-05 — the current Pi5 config undercuts §1

This document was written against the **Jetson** voice stack. Voice now runs on
the Pi5 (`pi5_voice_pkg`, PI5_VOICE.md), and the live config changes the
microphone conclusion below in a way §1 does not cover:

`src/pi5_voice_pkg/config/voice_params.yaml` sets `bt_profile: hfp` for the
boAt Stone on BOTH nodes. HFP is the Bluetooth *call* profile: the mic comes
back at **8-16 kHz narrowband**, which is worse input than the earphone §1
already blames — `stt_node.py`'s own docstring flags it ("Whisper accuracy
drops"). It is deliberate (the comment reads "Stone mic in use; a2dp would
kill it"), so it is a trade, not a bug: one device for both legs, at the cost
of the band the recogniser needs most.

If STT accuracy is the thing that hurts, this is the first lever, ahead of
every model change below: keep the Stone on **a2dp** (speaker only, full
quality) and put a **separate USB mic** on the Pi5 — a ReSpeaker array or a
USB speakerphone, exactly as §1 recommends. Then `input_device` points at the
USB mic and `output_device`/`bt_mac` keep the Stone.

Second lever, same file: `wake_detector: transcript_alias` with
`stt_provider: sarvam`. The acoustic wake word is implemented
(`wake/openwakeword_detector.py`) but switched OFF, so **every** utterance in
the room is transcribed by a cloud provider — the config's own comment calls
out the cost/privacy consequence. Turning on `wake_detector: openwakeword`
means nothing leaves the house until the wake word is heard, and it removes
the whole class of "the robot answered something it overheard".

## 1. The microphone is 70% of it (your earphone mic is the problem)

An earphone mic is designed for a mouth 5 cm away. Across a room it delivers
quiet, AGC-pumped, reverberant audio — exactly the input Whisper is known to
hallucinate on (it invents "Thank you." / "you" / whole sentences from noise
and silence, because it was trained on YouTube-style always-speech audio).

Buy (PRODUCT.md already ranks these, ~$35–70 total):
- **ReSpeaker USB 4-mic array** or a **USB conference speakerphone**
  (Jabra/Anker/eMeet class). A speakerphone is the sneaky-best pick: it does
  beamforming AND hardware echo-cancel, which also upgrades barge-in.
- Plug into the Jetson, then point `mic_preference` (voice_params.yaml) at it
  and keep audio routed through the `ec_mic`/`ec_speaker` PipeWire pair.

## 2. Whisper settings that cut hallucination (free, do these first)

**STATUS: ✅ all implemented on the Jetson (commit 28e0f58, 2026-07-06).**
The vocabulary primer is tunable in voice_params.yaml (`initial_prompt`);
the confidence filter drops segments with no_speech_prob > 0.6 AND
avg_logprob < −1.0. Do the §5 A/B with the real mic to measure the gain.

All in the Jetson repo (stt backend / voice_params.yaml). Each one targets a
specific failure you're seeing:

| Change | Why |
|---|---|
| `condition_on_previous_text=False` on the decode call | THE classic hallucination fix — stops one bad transcript from seeding the next |
| `no_speech_threshold` ~0.6 + drop segments whose `avg_logprob` < −1.0 | discards "transcripts" of silence/noise instead of publishing them |
| `initial_prompt="Rakhi, Chotu, Swiggy, Telegram, …"` (household vocabulary) | Whisper spells rare names right when primed; shrinks the wake-alias zoo |
| Raise VAD strictness: `min_speech_duration` up, SileroVAD threshold up | fewer half-syllable blips reaching Whisper = fewer inventions |
| Language pin `language="en"` (if not already) | stops random language-flip hallucinations |

## 3. Model ladder on the Orin 8GB (separate branch, one rung at a time)

Current: `whisper small` on CUDA. Options, in order of bang-for-buck:

1. **`distil-small.en` / `distil-medium.en`** — English-only distillations:
   ~2× faster than their teachers with equal-or-better English WER. Best
   accuracy-per-VRAM on an 8GB board sharing memory with YOLO + Kokoro.
2. **faster-whisper (CTranslate2)** — 2–4× throughput at same accuracy;
   biggest single speed lever. CAUTION: the container pip is pinned to the
   jetson-ai-lab index (CLAUDE.md gotcha 3) — install with
   `PIP_INDEX_URL=https://pypi.org/simple pip install --no-deps` and expect
   to resolve ctranslate2's CUDA wheel for JetPack manually.
3. **`medium.en`** — only after the mic array; on far-field audio the jump
   small→medium is big, but latency roughly doubles. Watch the ≤2s budget
   with `latency_replay.py --listen`.

Branch discipline (as you suggested): try each rung on a `voice-quality-*`
branch on the Jetson repo, A/B with the method in §5, merge only winners.

## 4. Training the custom wake word ("hey chotu" / "hey rakhi")

Today the NEURAL gate runs the stock `hey_jarvis_v0.1`. "Chotu" aliases only
cover the transcript fallback — a true "hey chotu" needs a trained model.
openWakeWord ships an **automatic synthetic-training pipeline** so you never
record thousands of samples:

1. Open openWakeWord's `automatic_model_training.ipynb` (in the
   dscripka/openWakeWord repo, runs on Google Colab free tier, ~1 hour).
2. Set the target phrase: `hey chotu` (also do a `hey rakhi` run while
   you're there). The notebook generates thousands of synthetic utterances
   (piper-sample-generator: many voices/speeds/pitches), mixes them with
   noise/impulse responses, trains, and validates against false-positive data.
3. Export the `.onnx` model. Copy to the Jetson:
   `scp hey_chotu.onnx rakhi24@<jetson>:~/robot/models/wake/`
4. Point the config at it (voice_params.yaml):
   `wake_models: ["hey_chotu"]` (openWakeWord loads by model-file stem from
   the wake models dir; keep `wake_threshold: 0.5` to start).
5. Rebuild voice_pkg + restart the stack (Jetson CLAUDE.md commands), then
   tune: false rejects → lower `wake_threshold` toward 0.35; false accepts →
   raise toward 0.65, retrain with more negative data if needed.
6. Keep the transcript aliases — they strip the name from the utterance and
   are the fallback if openWakeWord ever can't load.

## 5. Measure, don't vibe

- `python3 scripts/latency_replay.py --listen` while talking normally — the
  only mode showing real STT stages/timings.
- Every rejected utterance is logged: `Not addressed to me — ignored: "…"` in
  the launch log. Skim it weekly; misspelled wake words go into
  `wake_aliases`, hallucinated junk tells you §2 thresholds need work.
- A/B honestly: fixed script of ~20 phrases (near, far, music playing),
  count exact-transcript hits per configuration before/after each change.

## 6. TTS side (already decent — two cheap wins)

Kokoro is close to the quality ceiling for local TTS; when it *sounds* bad
it's usually the **speaker** (a tinny driver reads as robotic — PRODUCT.md
hardware rec #2, ~$20–30 full-range speaker) or clipping from playing near
100% volume. Keep everything routed to `ec_speaker` (CLAUDE.md gotcha 5) or
barge-in breaks. Voice/speed choices are tts_node params in the Jetson repo.
