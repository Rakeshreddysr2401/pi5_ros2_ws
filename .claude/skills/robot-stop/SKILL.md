---
name: robot-stop
description: Stop the robot (Mitra) safely — halt motion now, park the body while keeping chat/voice alive, or shut everything down before power-off. Use when the user says stop, park, shut down, power off, or turn the robot off.
---

Pick the smallest stop that does what the user asked, and say in plain words
what is still running afterwards.

## 1. "Stop moving" — right now

Any of these halts the wheels immediately; none needs a password:

- Say **"stop"** — the stop word halts speech and motion.
- Teleop: open `http://rakhi24-desktop.local:8091` and switch to **MANUAL**
  (it streams zero velocity and cancels nav2). Switch back to AUTO afterwards,
  or the brain cannot drive.
- From a shell on the Pi5 — **this first, it is instant and certain:**

```bash
curl -s -X POST "http://localhost:8091/mode?manual=on"    # wheels get zeros at 10 Hz
curl -s localhost:8091/mode                                # {"manual": true} = held
```

  Every driving tool also refuses while MANUAL is on, so nothing new starts.
  Set it back with `manual=off` only once the brain is idle
  (`curl -s localhost:8090/status | jq .runtime.user_input_pending` is false and
  no turn is mid-flight in `journalctl -u langrobo-brain -f`): a turn that
  was already deciding to move will move the moment AUTO returns.
- Also from a shell (cancels the brain's own motion tools and navigation):

```bash
source /opt/ros/jazzy/setup.bash; unset ROS_DISCOVERY_SERVER
ros2 topic pub --once -w 1 /voice/user_input std_msgs/msg/String "{data: 'stop'}"
```

  Usually ~4 s, but the CLI can time out before it is matched and deliver
  nothing (seen 2026-09-27) — check `journalctl -u langrobo-brain | grep "input: stop"`.
  Any new utterance cancels navigation and every blocking motion tool before
  the brain even thinks (`agent_node._on_user_input`).

## 2. Park — body off, brain still listening

```bash
cd ~/ros2_ws && ./scripts/fleet.sh stop
```

Removes the Jetson's `rover` container (camera, lidar, pose, nav — everything
there) and stops the laptop sim. The Pi5 brain, voice and Telegram stay up, so
the robot can still talk; it cannot see or move. Bring the body back with
`./scripts/fleet.sh rover` (a full `./rover up`, ~10 min).

## 3. Full shutdown — before switching the power off

```bash
cd ~/ros2_ws && ./scripts/fleet.sh down
```

Everything in 2, plus the Pi5's brain, micro-ROS and discovery units (asks
for the sudo password — the user must type it: tell them to run
`! ./scripts/fleet.sh down`). Then it is safe to power off, in any order.
Those units are enabled, so they come back by themselves on the next Pi5 boot;
after power-on, use the `robot-start` skill.

## Check it stopped

```bash
./scripts/fleet.sh status
```
