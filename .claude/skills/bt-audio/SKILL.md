---
name: bt-audio
description: Check, pair, switch or fix the robot's Bluetooth speaker + mic on the Pi5 (boAt Stone, earbuds, any paired device). Use when the user asks whether audio/voice is connected, wants to add or switch a speaker/headphones, or the robot cannot hear or speak.
---

You are helping with the robot's speaker + mic. Read PI5_VOICE.md § "The
speaker + mic have ONE owner" first if you have not this session. Explain
every result to the user in plain words (what is wrong, what you did, what
they need to do).

## The rules

- `audio_device_node` (part of `langrobo-voice`) is the ONLY thing allowed to
  connect/route Bluetooth. Never hand-run `bluetoothctl connect`,
  `pactl set-card-profile` or `wpctl set-default` while it is running — you
  would be fighting it. Read state freely; change state only through it.
- Pairing a NEW device is the one human step. Use the guided flow, never
  raw bluetoothctl: `./scripts/bt_speaker.sh pair` (interactive — the user
  must run it themselves: tell them to type `! ./scripts/bt_speaker.sh pair`).
- Already-paired devices need nothing: switch on → the node connects them.
- Never run the Pi5 voice trio while the Jetson's `ai_stack` voice role is up
  (double-speak / double-transcribe). Check with `./scripts/fleet.sh status`.

## Step 1 — status (always start here)

Run, in one go, and read all of it before saying anything:

```bash
systemctl --user is-active langrobo-voice; pgrep -af "pi5_voice_pkg" | grep -v pgrep | wc -l
bluetoothctl devices Trusted; bluetoothctl devices Paired
for m in $(bluetoothctl devices Trusted | awk '{print $2}'); do echo "== $m"; bluetoothctl info $m | grep -E "Name|Paired|Connected|Icon"; done
wpctl status | sed -n '/Sinks:/,/Streams:/p' | grep -E "\*|bluez|Stone|Buds"
```
If ROS is up: `source /opt/ros/jazzy/setup.bash && source install/setup.bash && timeout 5 ros2 topic echo --once --qos-durability transient_local --qos-reliability reliable /voice/audio_device`

Report as: which device is routed (or none), whether the voice service is
running, and which known devices are on/off.

## Step 2 — match the symptom

| Symptom | Cause | What to do |
|---|---|---|
| voice service not running | not installed / stopped | `./scripts/install_systemd.sh` once (sudo — user runs it), or `systemctl --user start langrobo-voice`. Foreground: `./scripts/run_voice.sh` |
| known device shows `Connected: no`, `Paired: yes` | switched off / out of range | tell the user to switch it on; the node retries every 10 s |
| known device shows `Paired: no`, `Trusted: yes` | it forgot the Pi (Stone after reboot; earbuds after reconnecting to a phone) | put it in **pairing mode**; the node re-pairs by itself within ~10 s. Only if the log says "refused re-pairing" → `./scripts/bt_speaker.sh pair` |
| `/voice/audio_ready` false, device connected | PipeWire never showed a sink | `journalctl --user -u langrobo-voice -n 50 -o cat`; usually the profile switch failed (`pactl` missing → `sudo apt install pulseaudio-utils`) |
| default sink is right, no mic (`Sources` empty) | device has no HFP, or `bt_prefer_mic: false` | check `bluetoothctl info` for `Handsfree`; set `bt_prefer_mic: true` in `voice_params.yaml`, restart the service |
| robot hears nothing but mic is routed | mic gain reset / too quiet | `wpctl status` source vol should match the device's entry in `bt_mic_gains` (Stone 4.00; unlisted devices `bt_mic_gain`); the node re-applies it — restart the service if not. Watch `/voice/debug_vad` |
| listener never goes quiet (`voiced=True` non-stop, "utterance hit the 20s cap") | mic gain too HIGH for this device, or steady room noise (cooler, fan) | add/lower the device's `bt_mic_gains` entry (Buds: 1.0); check idle rms with a 5 s `sd.rec` — must sit under `min_utterance_rms` 0.05 |
| two devices both on, wrong one used | preference | `bt_devices:` order in `voice_params.yaml` (first = preferred); the one already in use is kept until it disconnects |
| robot answers itself / double speech | Jetson voice also running | `./scripts/fleet.sh status`; stop one side |
| want a NEW speaker/headphones | never paired | user runs `! ./scripts/bt_speaker.sh pair` and follows the prompts |
| robot hears nothing and never answers | the wake gate is ON — it ignores everything until it hears "Mitra" | that is by design. `./scripts/wake_switch.py` shows the active model; `./scripts/wake_test.sh` gives a live score bar; `./scripts/wake_switch.py off` disables the gate |
| wake word never fires on this speaker | threshold set for a different mic (the Stone's 8 kHz HFP mic scores lower than wideband) | watch `[diag] asleep peak_wake_score` while the user calls it, then `./scripts/wake_switch.py mitra --threshold <between floor and peak>` |
| it answers "chepandi boss" over the command | the cue fired on a continued sentence | raise `wake_cue_delay_s` in `voice_params.yaml`, or `wake_cue: false` to silence it |

## Step 3 — verify after any change

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 topic pub -1 /voice/robot_speech std_msgs/msg/String "{data: 'Testing the speaker.'}"
ros2 topic pub -1 /voice/robot_speech std_msgs/msg/String "{data: '<|eou|>'}"
```
Ask the user if they heard it; for the mic, `ros2 topic echo /voice/debug_transcript`
and ask them to say something. Done means: heard AND transcribed.

## Config reference

`src/pi5_voice_pkg/config/voice_params.yaml` → `pi5_audio_device`:
`bt_devices` (preference list), `bt_prefer_mic`, `bt_mic_gain` + `bt_mic_gains` (per-device "MAC=gain"),
`wired_fallback` (USB headset name when nothing Bluetooth is up),
`poll_period_s`, `connect_retry_s`. Restart `langrobo-voice` after edits.
Roadmap for what comes next (echo cancellation, wake word, pairing by
voice/Telegram): VOICE_ROADMAP.md.
