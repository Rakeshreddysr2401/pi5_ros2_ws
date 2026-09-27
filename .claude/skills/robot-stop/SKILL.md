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
- From a shell on the Pi5:

```bash
source /opt/ros/jazzy/setup.bash; unset ROS_DISCOVERY_SERVER
ros2 topic pub --once -w 1 /voice/user_input std_msgs/msg/String "{data: 'stop'}"
```

(Any new utterance cancels navigation and every blocking motion tool before
the brain even thinks — `agent_node._on_user_input`.)

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
