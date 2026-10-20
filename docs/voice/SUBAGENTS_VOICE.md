# Voice in SubAgents, and what the Pi5 can take from it (2026-10-20)

The SubAgents app (Mac + browser) has voice that "just works". Kino's voice
on the Pi5 doesn't feel as good yet. This doc explains exactly how SubAgents
does STT/TTS, why it feels better, and which parts can move to the Pi5
pipeline (`pi5_voice_pkg`). Written so an agent on another machine can pick
it up without access to this Mac.

## Where the code is

| What | Repo | Branch |
|---|---|---|
| SubAgents (the one that works) | https://github.com/Rakeshreddysr2401/SubAgents | `harden/prod-hardening-fixes` |
| Kino brain + voice (this repo) | https://github.com/Rakeshreddysr2401/pi5_ros2_ws | `dev-1.6.11-review` |

Full SubAgents write-up: `docs/voice-stt-tts.md` in the SubAgents repo.
Clone both repos; no SSH access to the developer's Mac is needed for any of
this.

---

## 1. How SubAgents does voice

SubAgents has **no STT/TTS model of its own**. The browser does both, with
the Web Speech API, and the OS supplies the engines:

| Piece | Runs in | API | Engine |
|---|---|---|---|
| TTS | Browser | `speechSynthesis` | **macOS system voices (Apple)**, local, instant |
| STT | Browser | `webkitSpeechRecognition` | **Chrome: Google's cloud streaming recognizer.** Safari: Apple's |
| Wake word | Python thread | `openwakeword` + `sounddevice` | local ONNX, `alexa` |

Important correction to the usual assumption: **in Chrome, the STT is not
Apple, it's Google's.** Only TTS is Apple there. If the comparison is "Apple
STT is great in SubAgents but bad on the Pi", it's actually comparing Google
streaming STT (Chrome) against `kino-stt` (Apple `SFSpeechRecognizer`, en-IN)
or Sarvam on the Pi.

### Flow

```
openWakeWord hit ─► SSE /events {"type":"start_voice"} ─► browser starts SpeechRecognition
user speaks ─► recognizer endpoints by itself ─► final transcript ─► POST /chat
reply streams as text ─► on "done", speechSynthesis speaks the whole reply, sentence by sentence
```

### Settings that matter (SubAgents `web/src/components/chat/Composer.tsx`)

- `continuous = false`, `interimResults = false`, `lang = "en-US"`:
  one utterance, final text only, sent immediately.
- The recognizer is created once and reused.
- **Endpointing is the recognizer's job.** There's no VAD of our own; Google
  decides when the sentence ended.

### TTS reliability tricks (SubAgents `web/src/lib/tts.ts`)

1. Strip markdown before speaking.
2. Split into sentence chunks of ≤200 chars.
3. Speak one chunk at a time, chained on `onend` (and on `onerror`).
4. `cancel()` + `resume()` before every reply (unsticks a paused engine).
5. 60 ms gap after `cancel()` before the first chunk.
6. `resume()` keepalive every 10 s while speaking.

These are Chrome-specific bug fixes; they don't apply to the Pi as-is, but
items 1 to 3 do (see §3).

---

## 2. Why it feels better than the Pi5 (the real reasons)

1. **Microphone and DSP.** The browser opens the Mac's built-in mic at arm's
   length, with the browser's default echo cancellation, noise suppression
   and auto gain switched on. The Pi hears the room through a Bluetooth or
   conference mic (`VOICE_QUALITY.md` §1: "the microphone is 70% of it").
2. **Streaming STT with server-side endpointing.** Google's recognizer hears
   the audio as it arrives and decides when you stopped. The Pi cuts the
   utterance with Silero/WebRTC VAD first, then uploads a WAV, so a bad cut
   (clipped start, too-early end) can't be repaired by the recognizer.
3. **TTS is local and instant.** macOS voices start speaking in
   milliseconds, with no network. The Pi uses Sarvam (network, first sound
   ~0.8 s streamed) or Kokoro (RTF ~1.8, slower than real time).
4. **No wake gate on the text.** SubAgents' wake word only opens the mic;
   everything heard after it is sent. The Pi also gates on
   addressing/relevance, which can drop real commands.
5. **Simpler turn.** One utterance → one request → speak the final reply.
   No ROS topics, no device ownership, no robot motion competing for CPU.

---

## 3. What to try on the Pi5, best value first

These are proposals, not built. Each is one provider or one setting, in the
existing provider registry pattern (`stt_providers/`, `tts_providers/`,
`REGISTRY`, `from_config()`).

1. **Apple TTS from the Mac Mini (`mac_tts` provider).** This is the piece
   SubAgents has and the Pi doesn't. Mirror `kino-stt`: a small server on
   the Mac Mini (Swift, `AVSpeechSynthesizer.write(_:toBufferCallback:)`, or
   `say -o out.aiff --data-format=LEI16@22050`) on a new port, e.g. `:8092`,
   `POST /tts {"text"} → WAV`. On the Pi, `tts_providers/mac.py` returns
   `(float32 samples, rate)` and raises `ProviderUnavailable` on any failure,
   so `tts_node` falls back to local exactly as it does today. Run it as a
   LaunchAgent next to `com.kino.stt`, and have `e12b5`'s `ensure.sh` start
   it. English only; keep `sarvam_stream` for Telugu.
2. **Speak sentence by sentence.** Split the reply into sentences (≤200
   chars), synthesize and play the first one while the next is synthesized.
   First sound arrives after one sentence, not the whole reply.
3. **Strip markdown before TTS** (the regex set in SubAgents `tts.ts`), if
   the brain's replies can contain `**`, lists, links or code.
4. **Fix the mic before the model.** Score the same phrases through the
   current mic vs. a close USB mic with `scripts/voice_lab/score_set.py`
   before changing any STT provider.
5. **Give the endpointing a margin.** If transcripts lose the first or last
   word, add pre-roll (keep ~300 ms before VAD start) and lengthen the end
   silence before cutting. Measure with `score_set.py`.
6. **Optional: test Google STT as a baseline.** Run the same utterances
   through Chrome on the Mac to separate "the mic/room is the problem" from
   "the recognizer is the problem".

## 4. Working method for the agent picking this up

1. Read, in this repo: `CLAUDE.md`, `docs/voice/PI5_VOICE.md`,
   `docs/voice/MAC_STT.md`, `docs/voice/VOICE_ROADMAP.md`.
2. Read, in SubAgents: `docs/voice-stt-tts.md`, `web/src/lib/tts.ts`.
3. Build one item from §3 at a time behind its config switch; keep the
   existing fallback behaviour (provider raises `ProviderUnavailable` → local).
4. Add a test next to `src/pi5_voice_pkg/tests/test_apple_stt.py` (stand-in
   server + one optional live call).
5. Measure before and after with `scripts/voice_lab/score_set.py`; record the
   result in `docs/voice/VOICE_ROADMAP.md`'s decision log.
6. Don't run two voice stacks at once (`PI5_VOICE.md`: double-speak and
   double-transcribe).

Machine access (Pi5, Jetson, Mac Mini) is not in this doc on purpose: ask the
owner for SSH access instead of committing hostnames, usernames or keys.
