# Mac Mini STT — Apple's on-device recogniser for the Pi5

The Mac Mini already runs the LLM/VLM. It also has Apple's speech recogniser
(the engine behind Siri and Dictation), which runs on the Neural Engine. This
puts it on the LAN as a small HTTP service, **mitra-stt**. The Pi5 sends each
utterance to it, the same way it sends one to Sarvam, and gets text back.

```
 Pi5 (stt_node)                                     Mac Mini (mitra-stt.app, :8091)
 mic ─► VAD cuts one utterance ─► WAV ── POST /stt ──► SFSpeechRecognizer
                                                        en-IN, on-device, "Mitra" hint
 /voice/user_input ◄── wake/addressing gate ◄── {"transcript": "Mitra, go to the kitchen"}
        │
        └─ Mac down? the same utterance goes to stt_fallback (Parakeet, on the Pi)
```

| Piece | Where |
|---|---|
| Mac server | `scripts/mac_mini/stt_server/`: `main.swift` (the server), `build.sh`, `install.sh`, `ensure.sh` (run by `e12b4`), `com.mitra.stt.plist` (LaunchAgent), `Info.plist` |
| Pi5 provider | `src/pi5_voice_pkg/pi5_voice_pkg/stt_providers/apple.py`, registered as `apple` |
| Pi5 config | `src/pi5_voice_pkg/config/voice_params.yaml` (`stt_provider`), `~/ros2_ws/.env` (`MAC_STT_*`) |
| Tests | `src/pi5_voice_pkg/tests/test_apple_stt.py` (a stand-in server, plus one live call) |
| Score on your voice | `scripts/voice_lab/score_set.py` (row "Apple on-device (Mac Mini)") |

## What it can and cannot do

- **English only.** Apple has **no Telugu** recogniser: there is no `te-IN`
  locale at all (probed 2026-10-10, macOS 15.5). The Indian locales are `en-IN`
  (on-device) and `hi-IN` (Apple's servers only). This provider transcribes. It
  never translates, so Telugu speech stays on `sarvam`.
- **On-device.** Audio never leaves the house, unless you set `MAC_STT_SERVER=1`.
- **Fast.** On the Mac (M4) a sentence takes 0.02–0.25 s; add a few ms of LAN.
- **Name hint.** `stt_hotwords` ("Mitra") is sent as Apple's
  `contextualStrings` (plus "Hey Mitra"), so the name comes back as "Mitra"
  instead of "Metro".
- **Formatting.** It writes numbers and units the way a person types them
  ("Turn left 90° and move forward 1 m"). The brain's LLM reads that fine.

First check (2026-10-10, on the Mac): five robot commands, synthesised, then
also squeezed to 8 kHz like the Bluetooth HFP mic. All came back word-for-word,
the name included. **This is not yet scored on the owner's own voice** (step 2.9).

---

## 1. Mac Mini — install once (done 2026-10-10)

Needs only the Xcode Command Line Tools (`xcode-select --install`). Do this in
the Mac's own logged-in session (screen sharing is fine), because macOS asks
once for permission:

```bash
cd ~/PycharmProjects/pi5_ros2_ws/scripts/mac_mini/stt_server
./install.sh            # builds mitra-stt.app, asks for Speech Recognition, starts the LaunchAgent
curl -s localhost:8091/health     # "auth":"authorized", en-IN "on_device":true
```

- The LaunchAgent `com.mitra.stt` starts it at login and restarts it if it dies.
  Log: `/tmp/mitra-stt.log`. Remove it with `./install.sh uninstall`.
- **`e12b4` starts it too.** The alias (`~/.zshrc`) runs `ensure.sh` before
  llama-server. If speech is already up it says so. Otherwise it restarts the
  LaunchAgent (or opens the app), waits up to 10 s, and reports. It never
  blocks llama-server.
- After editing `main.swift`, run `./install.sh` again. If macOS asks for
  permission again, that is the ad-hoc signature changing. Click Allow.
- **Sleep is off** (`pmset sleep 0`, already set for llama-server).
- **The Mac must be logged in.** FileVault is on, so auto-login is disabled:
  after a power cut or reboot, mitra-stt does not run until someone logs in on
  the Mac. llama-server has the same requirement. Until then the Pi
  transcribes with its fallback (Parakeet) and logs the reason (2.10). For a
  planned reboot, `sudo fdesetup authrestart` comes back logged in.
- The macOS firewall is off. If you turn it on, allow `mitra-stt` when asked,
  or the Pi gets "connection refused".

**Why an `.app`?** macOS checks Speech Recognition permission against the
*responsible* process. If you run the bare binary from a terminal, macOS asks
the terminal, which has no usage description, and the process is killed
(`SIGABRT`, `TCC_CRASHING_DUE_TO_PRIVACY_VIOLATION`). As a bundle started by
`open` or launchd, it gets its own entry under
System Settings → Privacy & Security → Speech Recognition.

---

## 2. Pi5 — make it send its speech to the Mac

Run everything on the Pi5 as `rakhi24`, in `~/ros2_ws`.

### 2.1 Get the code onto the Pi

The Pi pulls from GitHub, so first commit and push from the Mac. Then, on the Pi:

```bash
cd ~/ros2_ws
git fetch origin
git checkout fix/dev-1.3.8-issues      # or whichever branch has stt_providers/apple.py
git pull
ls src/pi5_voice_pkg/pi5_voice_pkg/stt_providers/apple.py     # must exist
```

### 2.2 Python dependencies

None are new. `apple.py` uses `requests` and `numpy`, which Sarvam already
needs. To be sure:

```bash
python3 -c "import requests, numpy; print('ok')"
# missing? pip3 install --break-system-packages -r requirements.txt
```

### 2.3 Check that the Pi can reach the Mac

```bash
getent hosts singireddys-mac-mini.local                   # mDNS resolves (an IP prints)
curl -s -m 3 http://singireddys-mac-mini.local:8091/health
# {"ok":true,"auth":"authorized","locales":[{"id":"en-IN","on_device":true,...}]}
```

Do not continue until this prints `"auth":"authorized"`. If it fails, see
section 4. Always use the mDNS name, never an IP: DHCP has moved the Mac before
(OPERATIONS.md).

### 2.4 Check a real transcription from the Pi, without ROS

This uses a clip that is in git, so no microphone is needed:

```bash
curl -s --data-binary @data/test_pos/mitra_rishi.wav \
  'http://singireddys-mac-mini.local:8091/stt?lang=en-IN&context=Mitra,Hey%20Mitra'
# {"transcript":"Mitra","secs":0.13,"on_device":true}
```

Then run the same code path stt_node will use:

```bash
cd ~/ros2_ws/src/pi5_voice_pkg && python3 - <<'EOF'
import os, wave, numpy as np
from pi5_voice_pkg.stt_providers import REGISTRY
p = REGISTRY['apple'].from_config({'hotwords': 'Mitra'}, os.environ)
w = wave.open('../../data/test_pos/hey_mitra_samantha.wav')
pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
print(repr(p.transcribe(pcm, w.getframerate())))
EOF
```

### 2.5 Switch the provider

Edit `src/pi5_voice_pkg/config/voice_params.yaml`, in the `pi5_stt_node:` block:

```yaml
    stt_provider: apple       # was: sarvam  (Apple = English only; see top of this page)
    stt_fallback: sherpa      # keep: Mac down -> Parakeet on the Pi
    stt_hotwords: "Mitra"     # keep: also sent to Apple as the name hint
```

Leave the other STT keys alone. `stt_source_language` / `stt_target_language`
only matter to Sarvam and Soniox, and `wake_*` / `require_wake` work the same
on any provider.

### 2.6 Environment (optional)

The defaults are right for this house. Set these in `~/ros2_ws/.env` (stt_node
reads it at startup, as it does `SARVAM_API_KEY`) only to change them:

```bash
MAC_STT_URL=http://singireddys-mac-mini.local:8091/stt   # default
MAC_STT_LANG=en-IN                                        # default; en-US is also on-device
# MAC_STT_SERVER=1                                        # let Apple's servers help (audio leaves the house)
```

Keep `SARVAM_API_KEY` in `.env`. Switching back is then a one-word config change.

### 2.7 Build and restart voice

```bash
cd ~/ros2_ws
colcon build --symlink-install --packages-select pi5_voice_pkg
systemctl --user restart langrobo-voice
```

Only the voice unit needs a restart. The brain (`langrobo-brain`) is not
involved: it reads `/voice/user_input` and does not care which recogniser
produced it.

### 2.8 Verify on the robot

```bash
# 1. the provider loaded (not "apple unavailable at startup ... using local")
journalctl --user -u langrobo-voice -b -o cat | grep -E 'stt_provider|apple|fallback'
#    stt_provider = apple

# 2. speak to it ("Mitra, what time is it?") and watch, in two terminals:
source /opt/ros/jazzy/setup.bash && source ~/ros2_ws/install/setup.bash
ros2 topic echo /voice/debug_transcript     # the words Apple heard
ros2 topic echo /voice/stt_meta             # one JSON line per utterance
#    {"provider": "apple", "configured_provider": "apple", "fell_back": false, "latency_ms": ~150, ...}

# 3. the Mac sees the requests
ssh singireddy@singireddys-mac-mini.local tail -f /tmp/mitra-stt.log    # or tail it on the Mac
#    0.12s en-IN Mitra, what time is it?
```

`"fell_back": true` means the call to the Mac failed and Parakeet answered
instead. `fallback_reason` in the same line says why (section 4).

### 2.9 Score it on your own voice (before you keep it)

This uses the same 39 sentences as VOICE_LAB_2026-10.md, recorded with
`scripts/voice_lab/record_set.py`:

```bash
cd ~/ros2_ws && python3 scripts/voice_lab/score_set.py
# -> ~/voice_lab/recordings/results.md, rows for Sarvam, Parakeet, Whisper, Apple
```

Keep `apple` if its word-error rate is at or below Sarvam's for English speech.
For a Telugu-speaking household, keep `sarvam`.

### 2.10 Switching back

Set `stt_provider: sarvam` in `voice_params.yaml`, then repeat the commands in 2.7.

---

## 3. What happens when something fails

The Pi never goes deaf (CLAUDE.md rule 5):

| Situation | What the Pi does | Where you see it |
|---|---|---|
| Mac asleep / logged out / mitra-stt down | each utterance falls back to Parakeet; switches back to Apple on its own when the Mac returns | `stt_meta` `"fell_back": true`, `fallback_reason: mac stt request failed` |
| Mac up but Speech permission missing | refuses at startup; Parakeet for the whole session (restart voice once fixed) | journal: `apple unavailable at startup (... no Speech Recognition permission ...)` |
| Mac unreachable when the voice service starts | starts as `apple` anyway; each call falls back until the Mac is reachable | journal: `stt_provider = apple`, then per-call fallbacks |
| Clip is silence / noise | empty transcript, no turn (same as every provider) | `debug_transcript`: `(empty — filtered ...)` |
| Mac takes > 5 s (`TIMEOUT_S`) | that utterance falls back | `fallback_reason: ... timed out` |

## 4. Troubleshooting

| Symptom (on the Pi) | Cause → fix |
|---|---|
| `curl: (6) Could not resolve host` | mDNS: Pi or Mac off the network, or avahi down on the Pi → `systemctl status avahi-daemon`; check that both are on the same Wi-Fi |
| `curl: (7) ... Connection refused` | mitra-stt not running → on the Mac: `launchctl print gui/$(id -u)/com.mitra.stt \| grep state`; `cat /tmp/mitra-stt.log`; re-run `./install.sh`. Or the Mac's firewall is blocking it |
| `curl: (28) timed out` | Mac asleep or logged out after a reboot → log in on the Mac (FileVault, section 1) |
| `"auth":"denied"` / `"notDetermined"` | System Settings → Privacy & Security → Speech Recognition → enable mitra-stt, or run `./install.sh` on the Mac's screen and click Allow |
| HTTP 503 `no on-device model for ...` | `MAC_STT_LANG` is a locale without an on-device model (en-GB, hi-IN) → use en-IN/en-US, or `MAC_STT_SERVER=1` |
| Name heard as "Mira" / "Metro" | `stt_hotwords` empty → set it to `"Mitra"` (it becomes the context hint) |
| Telugu speech comes out as English nonsense | expected: Apple has no Telugu → `stt_provider: sarvam` |
| Journal: `unknown stt_provider 'apple'` | old code on the Pi → 2.1 (pull), then 2.7 (build) |

## 5. The HTTP API (the contract between the two halves)

Change `main.swift` and `apple.py` together or not at all.

```
POST /stt?lang=en-IN&context=Mitra,Hey%20Mitra[&server=1]
     Content-Type: audio/wav      body = one utterance, 16-bit PCM WAV, any sample rate
  200 {"transcript": "Mitra, stop.", "secs": 0.07, "on_device": true}
  200 {"transcript": ""}                       silence / no speech
  400 {"error": "empty body (send a WAV)"}
  503 {"error": "..."}                          recogniser failed / no model / timeout (15 s)

GET /health
  200 {"ok": true,  "auth": "authorized", "locales": [{"id": "en-IN", "available": true, "on_device": true}, ...]}
  503 {"ok": false, "auth": "denied", ...}
```

- One request per connection (`Connection: close`). Requests run concurrently.
- Port: the first argument to the binary (default 8091; `8090` is the brain's
  health API on the Pi, `8080` is llama-server).

## Notes / next steps

- macOS 26 adds `SpeechAnalyzer`/`SpeechTranscriber`, a newer and stronger
  on-device model. After the Mac upgrade it can replace `SFSpeechRecognizer`
  inside `main.swift` without changing the API above, so the Pi side is untouched.
- `SFSpeechLanguageModel` (macOS 14+) can be trained on the robot's own
  command phrases and place names for more accuracy. It is not done yet.
