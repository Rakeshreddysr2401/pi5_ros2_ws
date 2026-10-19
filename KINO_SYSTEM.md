# KINO — start here: the whole system on one page

> **The same file is in all three repos:** Jetson `~/rover/KINO_SYSTEM.md`, Pi 5
> `~/ros2_ws/KINO_SYSTEM.md`, laptop `~/kino_sim/KINO_SYSTEM.md`. If you change it,
> copy it to the other two (`diff` between them must be empty). Last checked
> 2026-10-19 (night).

**KINO** (Knowledge Is Never Out) is a home robot you talk to: "Kino, go near
the bottle and tell me what's on it". It understands the request, finds the
thing in its photos, drives there to within about 1 cm without touching
anything, and answers by voice or Telegram. A **twin** (the robot's hardware
simulated in Isaac Sim on the laptop) runs the robot's real software, so
navigation and agents are tested there before they reach the real robot.

## 1. The machines, and how to log in

| machine | address | log in | repo → GitHub, branch | its job |
|---|---|---|---|---|
| **Jetson Orin Nano** | 192.168.1.15 · `rakhi-jetson.local` | `ssh rakhi24@192.168.1.15` | `~/rover` → `-langrobo_perception-`, `rover-v1.1.16-review`; also `~/kino_sim` → `main` (the twin's stand-in drivers) | **the body**: camera, lidar, pose, map, nav2, exact moves, photo→goal. All in one Docker container: `rover` (real) or `rover_twin` (twin), image `orin-nav:1.2` (`orin-nav:1.1` = rollback only). Power mode 15 W |
| **Pi 5** | 192.168.1.16 (fixed) · `rakhi24-desktop.local` | `ssh rakhi24@192.168.1.16` | `~/ros2_ws` → `pi5_ros2_ws`, `dev-1.6.11-review` | **the mind**: LangGraph brain (3 agents), voice, Telegram, teleop page :8091, micro-ROS agent for the ESP32. Services start at boot (systemd) |
| **Laptop (Isaac)** | DHCP, 192.168.1.9 on 2026-10-18 · user `chotesh` | at its keyboard only (**no ssh server**) | `~/kino_sim` → `mitra_sim` on GitHub (rename to `kino_sim` pending), `main`; `~/IsaacLab` (release/3.0.0) | **the twin**: Isaac Sim 6.2 + Isaac Lab 3.0 in conda env `kino`, RTX 4050 (driver 580, held), ROS domain 42 |
| **Old laptop** | DHCP, 192.168.1.8 · user `rakhi24` | through the Jetson: `ssh rakhi24@192.168.1.15`, then `ssh rakhi24@192.168.1.8` | — (files pushed by `./rover view`) | **the RViz station**: `./rover view` (real) / `TWIN=1 ./rover view` (twin) open RViz here |
| **Mac mini** | DHCP · `singireddys-mac-mini.local:8080` | at the Mac (no ssh set up from the robot machines) | — | llama.cpp: Gemma, LLM + VLM (`--parallel 4`), kino-stt |

**Who can ssh to whom** (keys, no passwords; checked 2026-10-18):

| from ↓ to → | Jetson | Pi 5 | old laptop | laptop | GitHub push |
|---|---|---|---|---|---|
| laptop | yes | yes | via the Jetson | — | **no** (pushes go through the Jetson's clone: laptop `origin` = `rakhi24@192.168.1.15:kino_sim`) |
| Jetson | — | yes | yes (`./rover view`) | no (no server) | yes |
| Pi 5 | yes, by name (`fleet.sh`; not re-checked today, the Jetson was down) | — | **no** (not set up) | no | yes |

`sudo` needs the owner's password on every machine: the owner runs those commands.

## 2. The hardware

| part | connected to | what it gives |
|---|---|---|
| Intel RealSense **D555** | PoE, own subnet (192.168.11.55), to the Jetson | stereo IR 896x504 @30 (emitter OFF, passive), depth, colour 448x252 @15, IMU 200 Hz (the gyro is used) |
| Slamtec **RPLidar C1** | USB on the Jetson | 360° scans, 10 Hz, 720 beams: the pose reference |
| **ESP32** (firmware v2) | USB serial to the Pi 5, socket `usb-0:2:1.0` (run_microros.sh opens it by-path), 115200, micro-ROS | 4-wheel PID at 50 Hz, encoders, `/cmd_vel` with a 500 ms watchdog, `/wheel_state`, `/wheel_ticks` |
| 2× **HC-SR04** ultrasonic (front corners, 6.5 cm up, 28 cm apart; built 2026-10-19) | their OWN ESP32 (MicroPython, `~/rover/phase1/firmware/sonar/`) on USB socket `usb-0:1:1.0` (the wheels' ESP32 stays in `usb-0:2:1.0`: both CP2102 boards share one by-id) | `scripts/sonar_node.py` (service langrobo-sonar) -> `/sonar/left`, `/sonar/right` (`sensor_msgs/Range`, frames sonar_left / sonar_right, fov 0.52 rad), `/sonar/status`; +inf while the board is missing. Used by nav2's collision monitor and by reach / goal_exec (~/rover NAV_PLAN.md) |
| Pi 5 speaker + mic | the Pi 5 | voice (STT/TTS, wake word "Kino") |

## 3. Start and stop

### The real robot (ROS domain 0: the real wheels)

```bash
# Everything, from the Pi 5 (the usual way):
ssh rakhi24@192.168.1.16
~/ros2_ws/scripts/fleet.sh rover        # micro-ROS + the Jetson's ./rover up (camera → ... → nav → Studio → RViz)
~/ros2_ws/scripts/fleet.sh status       # who is up;  check = every link proved with data
~/ros2_ws/scripts/fleet.sh stop         # park: stop the body, keep the brain

# Or on the Jetson itself:
ssh rakhi24@192.168.1.15 && cd ~/rover
./rover up          # every layer in order, each gated (~10 min); restarts the container
./rover health      # every layer's rate in one pass (~15 s)
./rover view        # RViz on the old laptop
./rover stop        # tear down (saves the SLAM room first)
IMAGE=orin-nav:1.1 ./rover up           # roll back to the old image
```

`./rover` lists all its commands at the top of the script (one layer at a time:
`camera`, `lidar`, `pose`, `fused`, `slam`, `map`, `nav`, `vlm`, `reach X Y`, ...).

### The twin (ROS domain 42: cannot reach the real wheels)

The Jetson runs one stack at a time: stop the real one first (`./rover stop`).

```bash
# 1. Laptop (at its keyboard):
cd ~/kino_sim && ./sim up home_real     # ~1 min; add follow=true for an Isaac window that chases the rover
./sim status                            # every contract topic's rate
# 2. The robot's own stack on it -- from the Pi 5 (stack + a second, twin brain):
~/ros2_ws/scripts/fleet.sh twin up      #   (down / status)
#    or only the Jetson part:   ssh rakhi24@192.168.1.15 'cd ~/rover && TWIN=1 ./rover up'
#    First start on a new image: if the map gate fails, run it again (nvblox compiles its GPU code once, ~3 min).
# 3. Drive and judge (laptop):
./sim scenario home_real                # the world's goals, judged on the TRUE pose (a touch fails)
./sim goal 3.0 0.1                      # one goal, like RViz's 2D Goal Pose
./sim test contract                     # also: encoders, motion, truth
# 4. Stop:
./sim down;  ssh rakhi24@192.168.1.15 'cd ~/rover && TWIN=1 ./rover stop'
```

## 4. Rules (for people and agents)

1. **Domain 0 is the real robot's wheels.** The twin is domain 42; `./sim`,
   `TWIN=1 ./rover` and the twin brain refuse 0. Agents never publish on 0.
2. **Fix the robot, not the twin.** A bug seen on the twin is fixed in `~/rover`
   or `~/ros2_ws`, so the fix ships to the robot. The twin changes only when it is
   wrong about the hardware, from a measurement.
3. **The robot is KINO.** No "Mitra" and no Gazebo anywhere.
4. **Ask the owner first** before changing drivers, system settings, power mode or
   anything that needs `sudo`.
5. Each machine commits to its own repo and pushes. Old branches are kept as
   `archive/<name>` tags on GitHub (cleaned 2026-10-18).

## 5. Where everything is written down

| repo | read first | then |
|---|---|---|
| Jetson `~/rover` | `STARTUP.md` (after a power-on: all boxes, in order, what good looks like), `SYSTEM_OVERVIEW.md` (how it all fits, the topic contract), `OPEN_ISSUES.md` (what is open) | `OPERATIONS.md` (the runbook: calibration, diagnosis), `ARCHITECTURE.md`, `NAV_GUIDE.md`, `LOCALIZATION.md`, `ISAAC_ROS_4_6_UPGRADE.md`, `docker/README.md` (the images) |
| Pi 5 `~/ros2_ws` | `README.md`, `HOW_IT_WORKS.md`, `TODO.md` (open items at the top) | `ARCHITECTURE_LLD.md`, `OPERATIONS.md` (systemd, health API), `NETWORKING.md`, `INTEGRATION_GAPS.md`, `TELEGRAM.md`, `MAC_MINI_TASKS.md`, `docs/voice/` |
| Laptop `~/kino_sim` | `README.md`, `CLAUDE.md` (rules for the twin), `docs/STATUS.md` (where things stand, sim-real gaps) | `docs/IMPROVEMENT_LOOP.md` (the method), `docs/PLAN_AGENTS_NAV.md` (the plan), `docs/VERSIONS.md` (exact versions) |

## 6. Versions (2026-10-18)

- Laptop: Ubuntu 24.04.5, NVIDIA 580.178.04, Isaac Sim 6.2.0, Isaac Lab 3.0.0,
  Python 3.12, torch 2.11.0 + CUDA 13.0 (details: `~/kino_sim/docs/VERSIONS.md`).
- Jetson: image `orin-nav:1.2` = Isaac ROS 4.6, ROS 2 Jazzy, CUDA 13.2, VO = the
  packaged Isaac cuVSLAM (details: `~/rover/docker/README.md`).
- Pi 5: ROS 2 Jazzy on the host.

## 7. Known problems right now

- **The Jetson stops dead every few hours**: 2026-10-18 five times; 2026-10-19 at 12:07,
  16:30 and 21:46. The second before, `~/netwatch` shows nothing wrong (input 5.09 V
  at ~1.2 A, 53 °C, load ~2, WiFi -38 dBm) and the journal just ends: a power cut or a
  hard hang, not overheating or overload. After a reboot read `journalctl -b -1` and
  the newest `~/netwatch/*.log` (named 1970... until the clock syncs).
- **The Pi 5 is under-powered**: "Undervoltage detected!" in its kernel log; it dropped
  off at 21:24 on 2026-10-19. Needs a 5 V / 5 A supply (Pi 5 `TODO.md` item 0).
- The robot drives over a 1 cm tablet on the floor (below the lidar; rover
  `OPEN_ISSUES.md` #3).
- The twin's cuVSLAM once lost itself in turns (2026-10-19, a degraded twin process);
  not reproduced since: if VO goes wild, `./sim vocheck`, restart the twin (laptop
  `docs/STATUS.md`, gap 1).
- Owner to-dos: rename GitHub `mitra_sim` → `kino_sim` (then update remotes); run
  `scripts/mac_mini/migrate_to_kino.sh` (in the Pi 5's `~/ros2_ws`) on the Mac; restart `langrobo-brain` on the
  Pi 5 (sudo); install the sonar service (Pi 5 `TODO.md` 0b); delete `orin-nav:1.1`
  (58 GB; the real robot has run on 1.2 since 2026-10-18).
