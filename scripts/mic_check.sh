#!/usr/bin/env bash
# Is the mic actually hearing you? Records a few seconds and reports levels.
#
#   ./scripts/mic_check.sh          # 5 seconds
#   ./scripts/mic_check.sh 8        # 8 seconds
#
# TALK while it records — normally, from where you usually stand. It reads
# whatever audio_device_node made the default mic, so it tests the same path
# the robot listens on, and it runs happily alongside the voice service.
#
# Note: a speakerphone like the boAt Stone gates its own mic, so SILENCE can
# come out as pure digital zeros. That is why this asks you to speak — a dead
# mic and a gated one look identical when the room is quiet.

set -uo pipefail
cd "$(dirname "$0")/.."
SECONDS_TO_RECORD="${1:-5}"

python3 - "$SECONDS_TO_RECORD" <<'PY' 2> >(grep -vE 'device_discovery|GetGpuDevices' >&2)
import sys, time
import numpy as np
import sounddevice as sd

secs = float(sys.argv[1])
dev = next((i for i, d in enumerate(sd.query_devices())
            if d["name"] == "pipewire" and d["max_input_channels"] > 0), None)
print(f"mic: {sd.query_devices(dev)['name'] if dev is not None else 'system default'}")
for t in (3, 2, 1):
    print(f"  recording in {t}...", end="\r", flush=True)
    time.sleep(0.7)
print(f"  TALK NOW — {secs:.0f} seconds        ", flush=True)

x = sd.rec(int(secs * 16000), samplerate=16000, channels=1, dtype="int16", device=dev)
sd.wait()
f = x[:, 0].astype(np.float32) / 32768.0

rms, peak, zeros = float(np.sqrt(np.mean(f ** 2))), float(np.abs(f).max()), float(np.mean(f == 0))
print(f"\nlevel : rms {rms:.4f}   peak {peak:.2f}   digital-silence {zeros:.0%}")
print("per second:", "  ".join(
    f"{np.sqrt(np.mean(f[i*16000:(i+1)*16000] ** 2)):.3f}" for i in range(int(secs))))

# The robot drops anything under min_utterance_rms (0.05) as noise.
if peak == 0.0:
    verdict = "NOTHING at all — the mic is delivering pure silence. Check it is\n" \
              "        connected (./scripts/bt_speaker.sh status) and that this is the\n" \
              "        right device; if it is a speakerphone, make sure it is not muted."
elif rms >= 0.05:
    verdict = "GOOD — your voice is well above the robot's 0.05 noise gate."
elif rms >= 0.02:
    verdict = "WEAK — audible but close to the 0.05 gate; speak closer, or raise this\n" \
              "        device's gain in voice_params.yaml (bt_mic_gains: \"MAC=gain\")."
else:
    verdict = "TOO QUIET — under the 0.05 gate, so the robot would throw it away.\n" \
              "        Raise this device's gain in voice_params.yaml (bt_mic_gains)."
print(f"\nverdict: {verdict}")
PY
