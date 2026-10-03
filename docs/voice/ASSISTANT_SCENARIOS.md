# Assistant scenarios — what each everyday use needs from the mic, the speaker and the software

Owner, 2026-10-04: *"go through general scenarios — what all is required with the
mic and speaker — and add them neatly."* This is the checklist; VOICE_ROADMAP.md
holds the phases and their exit tests. Hardware decision stands (VOICE_ROADMAP §0):
**the boAt Stone 650 Bluetooth speaker + mic is the audio device.** No wake-word
work here — the owner will bring a properly trained model; until then the name
is matched in the transcript (`transcript_alias`).

## The one hardware fact every scenario runs into

A Bluetooth speaker with a mic is in ONE of two modes at a time:

| mode | speaker sound | mic | used for |
|---|---|---|---|
| **HFP** (call / headset) | 8 kHz mono — phone-call quality | **on** | listening + talking (now) |
| **A2DP** (music) | 44–48 kHz stereo — good | **off** | music |

So: while the robot listens, music sounds like a phone call; in good-music mode
it cannot hear you. Measured 2026-10-03: in HFP the "Mitra" wake model scored
0.02 on the owner's voice; Whisper with the `Mitra` hotword found the name 20/20
on simulated 8 kHz speech.

## Scenarios

`✅ works` · `⏳ later` · `🚫 not possible on this hardware`

| # | Scenario | Mic needs | Speaker needs | Software needs | Status |
|---|---|---|---|---|---|
| S1 | **Ask a question** ("Mitra, what time is it?") | hear the name + sentence | speak the reply | STT → name gate → brain → TTS | ✅ Whisper tiny.en + hotword, Piper (65879e2) |
| S2 | **Follow-up without the name** ("…and tomorrow?") | stays open after a reply | — | follow-up window (9 s) after each reply | ✅ `follow_up_window_s` |
| S3 | **Ignore the TV / people talking** | — | — | answer only when the name is in the text | ✅ `require_wake: true`, hotword-biased Whisper |
| S4 | **"Stop" while it is talking** | mic is muted while it talks (its own voice) | stop instantly | stop word → `/voice/tts_stop` | ⏳ by voice; ✅ via Telegram / a new turn |
| S5 | **Volume up / down / set / mute** ("louder", "volume 40", "mute") | — | change the speaker's level | `speaker_volume` tool → audio owner sets the PipeWire sink volume; remembered per device | ✅ live 2026-10-04 |
| S6 | **Which speaker? / connect / switch** ("connect my OnePlus buds", "use the boAt") | follows the device | follows the device | `bluetooth_device` tool → audio owner: list, connect, make default; mic follows if the device has one | ✅ list + connect live; switching to another device untested |
| S7 | **Pair a new device** ("pair a new speaker") | — | — | scan → pair → trust → connect (device must be in pairing mode) | ✅ built; not yet tried with a new device |
| S8 | **Speaker switched off / out of range** | fall back | fall back to the Pi's output, say so | audio owner already reconnects every 10 s | ✅ reconnect; ⏳ spoken notice |
| S9 | **Play a song** ("play Arijit Singh") | — | music | `music` tool → `media_node`: yt-dlp search → GStreamer stream | ✅ live (~6-8 s to sound) |
| S10 | **Pause / resume / next / stop / what's playing** | — | — | same tool, `media_node` commands + state | ✅ live |
| S11 | **Music volume** ("quieter") | — | — | `speaker_volume` while music plays changes the music level | ✅ one knob: the speaker volume |
| S12 | **Robot talks while music plays** (a reply, a reminder) | — | music dips, voice on top, music returns | ducking on `/voice/tts_speaking` and `/voice/user_input` (25 %) | ✅ built; ear test pending |
| S13 | **"Stop" / a question over loud music** | the mic hears the music itself (same box) | — | needs echo cancellation with music as a reference | 🚫 in HFP at full volume; ⏳ low-volume test. Pause from Telegram / phone works |
| S14 | **Reminders, timers, announcements** | — | speak at the time | brain → TTS (music ducks) | ⏳ (existed; cut in the minimal brain) |
| S15 | **Quiet hours** (lower at night) | — | capped volume | volume cap by clock | ⏳ |
| S16 | **Phone call through the robot** | HFP | HFP | Phase 6 | ⏳ |

## Contracts (new, additive — VOICE_ROADMAP cross-cutting rules)

| topic | type | from → to | meaning |
|---|---|---|---|
| `/audio/cmd` | String (JSON) | brain → audio owner | `{"op":"volume","set":40}` / `{"op":"volume","change":-10}` / `{"op":"mute","on":true}` / `{"op":"bt","action":"list"\|"connect"\|"pair","name":"buds"}` |
| `/audio/state` | String (JSON, latched) | audio owner → brain | `{"device","mac","profile","volume","muted","known":[...],"last":{op,ok,msg}}` |
| `/audio/music_cmd` | String (JSON) | brain → media_node | `{"op":"play","query":"..."}` / `pause` / `resume` / `stop` / `next` |
| `/audio/music_state` | String (JSON, latched) | media_node → brain | `{"playing","paused","title","query","error"}` |

Every command degrades to a spoken reason when the device, network or tool is
absent (CLAUDE.md #5) — never a crash, never silence.

## Built 2026-10-04 -- live check through the real brain

| said (typed onto /voice/user_input) | robot said | tool the model called |
|---|---|---|
| set the volume to 50 | I have set the volume to fifty percent. | speaker_volume(level=50) |
| a little louder | I have turned the volume up a little. | speaker_volume(change=10) |
| which speaker are you using? | I am currently using the boAt Stone 650. | bluetooth_device(status) |
| play Kesariya by Arijit Singh | I am playing Kesariya by Arijit Singh. | music(play) |
| what song is this? | That is Kesariya from the movie Brahmastra. | music(status) |
| what is the capital of France? | The capital of France is Paris. | (none) |
| stop the music | I have stopped the music. | music(stop) |

Files: `pi5_voice_pkg/audio_control.py` (pure), `audio_device_node.py`
(/audio/cmd), `media_node.py` (music), `langrobo_core/tools/audio.py`
(3 tools on chat, CAP_MEDIA: owner + family). Tests: voice 79, core 446.
Still to try with the owner: by voice through the mic, a second device
(OnePlus Buds), pairing a new device, how the 25 % duck sounds.
