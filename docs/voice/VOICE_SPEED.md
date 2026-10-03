# Voice speed and clarity — measured, analysed, built (2026-10-04)

Owner: *"Disable the wake word. Make STT and TTS fast, without delay. Telegram and
LangGraph Studio answer fast and clean; voice does not. Analyse, use the Jetson if
the Pi 5 is too slow, check all the constraints, look at every Telugu model, and
give me a clean final product."* Branch `dev-1.5.0-voice`.

## 1. Where the time went (measured on the robot)

"What is the time now?" spoken in Telugu ("మిత్ర, ఇప్పుడు సమయం ఎంత?"):

| stage | before | after | how |
|---|---|---|---|
| end of speech detected | 0.60 s | 0.60 s | 600 ms of silence (kept: shorter cuts sentences at a pause) |
| **listening** (Sarvam `saaras:v3`, Telugu → English) | 0.35 s (+0.11 s TLS) | **0.35 s** | already fast; connection now kept warm |
| is it for me? (no wake word) | — | **0.67 s** (0 s if the name is said) | brain LLM, grammar yes/no |
| **brain** (question → first reply sentence) | **3.5–4.3 s** | **1.6–1.9 s** | the clock rode a tool = 2 LLM calls; now `[Time now: …]` is on every turn |
| **speaking** (reply sentence → first sound) | **~1.8 s** | **~0.8 s** | streamed: translate on a warm session + `bulbul:v3` over one WebSocket |
| **finish speaking → robot starts answering** | **≈ 6.9 s** | **≈ 4.2 s** (≈ 3.6 s with the name) | |
| "heard you" feedback | none for ~7 s | **chime ≈ 1.6 s** after you stop | `heard` cue |

Why Telegram and Studio felt fast: they skip both voice legs (≈ 2.4 s) and show text
as it streams. The brain is the same one, on the same Mac mini.

Correctness found on the way: asked the time twice, the brain answered the second
time **from its history** ("five oh eight", 20 minutes late). Fixed by the clock stamp.

## 2. Telugu models — what exists, and why this build uses Sarvam

| | option | where it runs | speed | quality / notes |
|---|---|---|---|---|
| **STT** | **Sarvam `saaras:v3`** (REST, translate mode) | cloud | **0.35 s** for a 2 s sentence | Telugu + code-mixed, straight to English for the brain — **used** |
| STT | Sarvam `saaras:v3-realtime` / `v4` (WebSocket) | cloud | sub-250 ms partials, server VAD | worth it only if end-of-speech moves to the server; REST is already 0.35 s |
| STT | `vasista22/whisper-telugu-base` / `-large-v2` | Pi CPU / Jetson GPU | base: slower than real time on the Pi; large needs the GPU | Telugu text only — still needs a translation step |
| STT | AI4Bharat IndicConformer-600M | Jetson GPU (NeMo) | GPU-bound | strong Telugu ASR; heavy; GPU shared with nvblox/cuVSLAM |
| **TTS** | **Sarvam `bulbul:v3` WebSocket** | cloud | **first audio 0.18 s**, sentence 0.7 s | natural Telugu (voice `ritu`) — **used** |
| TTS | Sarvam `bulbul:v3` REST | cloud | 0.85 s per sentence, nothing until done | the previous path |
| TTS | AI4Bharat IndicF5 / Indic Parler-TTS | Jetson GPU | seconds per sentence | good quality, large models |
| TTS | AI4Bharat Indic-TTS (FastPitch/VITS), MMS-TTS | Pi / Jetson | real-time on CPU possible | MOS ~3.6–3.9; robotic next to Bulbul |
| TTS | Piper / Kokoro | Pi CPU | Piper RTF ~0.3 | **English only** — the offline fallback |
| translate | Sarvam translate (Mayura) | cloud | 0.6 s on a warm session | accurate; the brain's Gemma 12B writing Telugu itself took 2.45 s and got the time wrong |

**Why not the Jetson:** its GPU already runs nvblox, cuVSLAM and the depth pipeline
for navigation; a 600 M-parameter ASR or a Telugu TTS model there competes with the
robot's eyes, and every open Telugu option measured or published is slower *and*
weaker than Sarvam's 0.35 s / 0.18 s. The Pi 5 is not the bottleneck — Sarvam's
work is in the cloud, the Pi only streams audio. Local models remain the right
**offline** fallback (English Whisper + Piper, already wired).

Sources: [Sarvam streaming TTS](https://docs.sarvam.ai/api-reference/text-to-speech/stream),
[Sarvam realtime STT](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming),
[Bulbul](https://docs.sarvam.ai/api-reference-docs/models/bulbul),
[whisper-telugu-base](https://huggingface.co/vasista22/whisper-telugu-base),
[Indian open-source voice AI 2026](https://caller.digital/blog/open-source-voice-ai-india-sarvam-ai4bharat-bhasini-2026).

## 3. No wake word — how it stays clean

`addressing_mode: llm` (stt_node + `relevance.py`):

1. The name ("Mitra", or "Friend," as Sarvam translates it) → accepted at once.
2. Music playing + a bare control ("Louder.", "Next song", "Pause it") → accepted at once.
3. Anything else → one yes/no to the brain's LLM with the robot's last words and
   what is playing as context. Real room audio + requests: **28/30 right**, 0.67 s.
4. LLM unreachable → ignored unless named. The TV can never take over.
5. Accepted → the **heard chime**, then the brain.

Follow-up answers ("Five minutes." after "For how long?") pass through rule 3 with
the robot's question as context.

## 4. Constraints

| constraint | effect | status |
|---|---|---|
| boAt Stone is the mic **and** the speaker (owner's choice) | HFP: 8 kHz mic, phone-quality sound; music sounds like a call | kept |
| the Stone cancels its own playback in hardware | an acoustic self-test is impossible on it; latency is measured per stage | measured per stage |
| Wi-Fi drops (SSH lost ~6 times overnight) | spikes in every cloud call | **owner, with sudo:** `sudo nmcli con mod "Airtel_Singireddy's" 802-11-wireless.powersave 2 && sudo nmcli con up "Airtel_Singireddy's"` |
| Sarvam credits | out of credits = English fallback, announced once | new key in `.env` 2026-10-04 |
| Mac mini runs the brain **and** the relevance check | under load the check took up to 3 s and was ignored | timeout 3 s, safe default |

## 5. Switches

| want | set in `voice_params.yaml`, then `systemctl --user restart langrobo-voice` |
|---|---|
| wake word back (name only) | `addressing_mode: name` |
| no chime | `heard_cue: false` |
| non-streamed Sarvam voice | `tts_provider: sarvam_translate` |
| fully local, English | `stt_provider: local`, `tts_provider: piper` |
