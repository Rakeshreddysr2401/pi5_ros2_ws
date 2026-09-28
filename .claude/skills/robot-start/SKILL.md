---
name: robot-start
description: Bring the whole robot (Mitra) up and prove it works — Pi5 brain + voice, Jetson perception/nav (./rover up), ESP32 wheels, Mac Mini LLM, Studio, laptop RViz. Use after a power cycle, when the user says start / bring up / wake the robot, or asks whether everything is running.
---

You are bringing the robot up. Explain each result in plain words: what is up,
what is not, and the one thing the user has to do (if anything).

## Rules

- **One command does it:** `./scripts/fleet.sh rover` (run in `~/ros2_ws` on the
  Pi5). It starts micro-ROS, and runs `./rover up` on the Jetson only when the
  Jetson stack is not already up — `./rover up` restarts the container, so never
  run it by hand against a healthy stack.
- **Prove it with `./scripts/fleet.sh check`**, not with `status`. `check`
  measures every link with data over ROS from the Pi5 (no ssh), and says what
  to run for each FAIL. It is read-only: nothing moves, nothing speaks.
- Never start a second voice copy. Voice is the `langrobo-voice` user unit;
  `systemctl --user restart langrobo-voice` is the only restart.
- Never drive the robot to "test" it unless the user is watching and asked.
- The Pi5 needs a password for sudo. The brain runs as `rakhi24` with
  `Restart=always`, so a restart without sudo is:
  `kill -INT $(systemctl show langrobo-brain -p MainPID --value)` (back in ~15 s).

## Step 1 — where are we

```bash
cd ~/ros2_ws && ./scripts/fleet.sh status
```

After a power cycle the Pi5 units are already up (they are enabled) and the
Jetson shows `rover container stopped`. That is normal.

## Step 2 — bring up

```bash
./scripts/fleet.sh rover
```

Takes ~10 min from cold: each Jetson layer prints a `GATE` with OK/FAIL
(camera → lidar → pose → fused → slam → map → nav → vlm → wheels → Pi5 →
Studio → laptop RViz). **A FAIL stops the run and names its layer** — fix that
one layer (the Jetson's `~/rover/STARTUP.md` has the physical fix for each),
then re-run just it: `ssh rakhi24@rakhi-jetson.local 'cd ~/rover && ./rover <layer>'`.

Common first-boot failures:

| layer | means | fix |
|---|---|---|
| camera | D555 not streaming (it answers ping while dead) | power-cycle the D555's PoE, **wait ~60 s** (it boots slower than `up` waits), then `./rover camera` alone and the remaining layers by hand (Jetson STARTUP.md "Continue by hand" — pose with `SLAM=false`). Don't re-run `up`: it restarts the camera |
| slam | `cuVSLAM is already publishing map -> odom` | pose ran without `SLAM=false`: `SLAM=false ./rover pose; ./rover fused; ./rover slam` |
| nav | costmaps 0.0 Hz after `./rover nav` | lost lifecycle reply under load — run `./rover nav` again; then `./rover logs reach` must say `pose OK` |
| lidar | `/dev/ttyUSB0` missing | reseat the RPLidar USB |
| wheels | no `/wheel_state` | ESP32 off or not on WiFi — power-cycle it; `systemctl status langrobo-microros` |
| view | laptop not found / nobody logged in | log in on the laptop desktop, then `ssh rakhi24@rakhi-jetson.local 'cd ~/rover && ./rover view'` |

## Step 3 — prove it

```bash
./scripts/fleet.sh check
```

Every line OK = ready. Then a no-motion live turn (answers through the speaker):

```bash
source /opt/ros/jazzy/setup.bash; unset ROS_DISCOVERY_SERVER
ros2 topic pub --once -w 1 /voice/user_input std_msgs/msg/String "{data: 'what do you see?'}"
journalctl -u langrobo-brain -f -o cat | grep -E "Step message \[AI|→ TTS"
```

(`-w 1` matters: without it `--once` often publishes before the brain is matched and the turn is silently lost.)

## Step 4 — tell the user how to use it

- **Talk:** say "Mitra" in the sentence ("Mitra, come to the kitchen"). The
  acoustic wake gate is off for now; the name is matched in the transcript.
- **Telegram:** message the bot (allowlisted users only).
- **Studio:** on the laptop `ssh -N -L 2024:localhost:2024 rakhi24@rakhi24-desktop.local`,
  then open `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`.
  Studio runs the same graph and can drive — stop the brain first if driving from it.
- **Traces:** https://smith.langchain.com → project `pi5` (runs `turn:voice`, `turn:telegram`).
- **RViz:** on the laptop; `./rover view --restart` (on the Jetson) after a config change.

## If `fleet.sh status` says `jetson: unreachable`

First check it is not just the NAME: after a power cycle the Jetson's avahi can
rename it `rakhi-jetson-3.local` (name clash). `ssh rakhi24@192.168.1.15`
answers → `ssh rakhi24@192.168.1.15 'sudo systemctl restart avahi-daemon'`, and
`getent hosts rakhi-jetson.local` resolves again. Note `fleet.sh rover` still
prints "ROVER mode up" when the Jetson's `up` FAILED — read the line above it.

## If ssh to the Jetson or laptop hangs

Almost always the **ssh agent**, not the machine: a stale `SSH_AUTH_SOCK`
makes every login hang at authentication with no error. Test with
`SSH_AUTH_SOCK= ssh rakhi24@rakhi-jetson.local uptime` — if that answers at
once, the agent was it (fleet.sh already bypasses the agent). `fleet.sh check`
never needs ssh: it reads everything over ROS.
