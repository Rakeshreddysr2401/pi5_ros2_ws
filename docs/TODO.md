# TODO — pending on-device work

## 2026-11-17 — Jetson <-> Pi 5 on the Ethernet cable (live)

Robot traffic (ROS, domain 0) now runs on a direct cable, not WiFi: Jetson 10.0.0.1, Pi 5
10.0.0.2, both NM profile `kino-link`, MTU 1500. 935 Mbit/s vs 135 on WiFi; the Jetson sees all
7 Pi 5 nodes and `/sonar/left` 15.2 Hz. How and why: [NETWORKING.md](NETWORKING.md) "The Jetson
<-> Pi 5 cable". The D555 is on the Jetson's USB 3 since 2026-11-16.

- **Owner will say when**: test with WiFi off on both (robot body + nav should keep working;
  Mac Mini, internet, phone teleop and RViz will not).
- Not tested: full `./rover up` + driving with the Pi 5 on the cable.
- The RViz laptop (WiFi only) no longer sees the Pi 5's own topics; `KINO_LINK=wifi` for one run.
- **Away from home WiFi (owner's goal):** still needs WiFi today: Mac Mini by mDNS name
  (`singireddys-mac-mini.local` :8080/:8091), internet (web search, api.sarvam.ai), phone teleop,
  RViz. Proposed: a travel router on the robot (its own network, uplink = phone hotspot) +
  Tailscale (reach the Mac Mini at home from anywhere), brain tries local -> Tailscale -> says
  "offline". Waiting on the owner: does the Mac Mini travel or stay home; buy a router or not.

## 2026-11-15 — Pi 5 + Jetson check (code, links, wasted CPU)

Checked: pyflakes clean (the 7 "unused" imports are re-exports / fixtures), 895 tests pass
(core 722, voice 143, bridge 30), every service up, and seen from the Jetson: `/wheel_state`
20.0 Hz, `/sonar/*` 15.3 Hz, clocks 8-10 ms apart. The Jetson's side: `~/rover/docs/OPEN_ISSUES.md`.

- **Fixed** `scripts/sonar_node.py` (307bd53): a 100 Hz timer polled the serial port, 7.6 % of a
  core and this Pi's largest idle load; a reader thread now sleeps until data comes: 1.5 %.
- **Fixed** the DDS setup (df254f4): domain / RMW / the variables to unset were copied into nine
  scripts, not all alike. One place now: `kino_dds [domain]` in `scripts/ros_env.sh`.
- **New on the Jetson**: `./rover clock` (also a gate in `./rover up`) -- nav2 drops our sonar
  stamps when the two clocks are more than 0.2 s apart.
- **Owner, please look**: `scripts/servo/sts_bench.py`, `sts_chain.py`, `sts_range.py`,
  `sts_test.py` are EMPTY files (0 bytes, dated 2026-11-12 22:53; only the README has text).
  They were never committed, so git has no copy. Most likely a brown-out before the files
  reached the SD card (item 0 below). They need to be written again.
- Still under-powered: 3 "Undervoltage detected!" in the first 2 minutes of today's boot (item 0).
- Memory, all in use: `stt_node` 1.3 GB (the sherpa fallback model is loaded at start so a
  fallback turn is instant), `tts_node` 0.45 GB, `agent_node` 0.45 GB, desktop ~0.4 GB;
  4.6 GB free. Loading the fallback only on first use would free ~0.9 GB at the cost of a
  few seconds on the first fallback turn: owner's call.


## NOW — open items, 2026-10-25 (most urgent first)

### 0. This Pi 5 is UNDER-POWERED (found 2026-10-19 night) -- most urgent
- The kernel log says "Undervoltage detected!" again and again: 16:35, 17:38, 21:24 --
  the minute it dropped off the network -- and 21:26 after it came back
  (`journalctl -k | grep -i voltage`). It now feeds two ESP32s over USB (wheels + sonar).
- Fix (owner): the official 27 W USB-C supply (5 V / 5 A) or an equal 5 V 5 A source.
  Below that the Pi 5 also caps its USB ports' current; every brown-out drops the
  wheels' link, the sonars and the brain.

### 0b. Front ultrasonic sensors -- BUILT 2026-10-19
- Hardware: two HC-SR04-type sensors (run at 3.3 V, no dividers needed there) on their
  OWN ESP32 in USB socket `usb-0:1:1.0` (keep the wheels' ESP32 in `xhci-hcd.1-usb-0:2:1.0` since 2026-11-17: the
  two CP2102 boards share one by-id name; run_microros.sh uses by-path).
- Firmware: MicroPython 1.24 + `~/rover/firmware/sonar/main.py` (update over USB
  with `upload_main.py`). Node: `scripts/sonar_node.py` -> `/sonar/left`, `/sonar/right`.
- Service installed and enabled 2026-10-25 (`scripts/setup_lyrical_root.sh`): /sonar/left and
  /sonar/right at 15 Hz, and they come back after a reboot.
- Owner to confirm: S1 (TRIG GPIO5) is the LEFT one; both point straight ahead, level.

### 1. The Jetson stops answering a few minutes after boot (since the power change)
- Before today's power-source change it ran 3 h+ without trouble. Since then it
  went down 3 times: ~9 min, ~2 min, ~1.5 min after boot, each time while doing
  only light work (a package-list download in a throwaway container). Power LED
  stays green; it does not come back by itself; it drops off WiFi entirely
  (no ARP from the laptop or the Pi 5). Happened on the new supply (15 W mode)
  and on the recommended charger (25 W mode).
- No shutdown record before any of those boots (`last -x`). Until 07:11 the
  system log was RAM-only, so the first two crashes left no log. **Persistent
  journal is now on** (`/var/log/journal`): after the next power-up, read
  `journalctl -b -1 -n 200` FIRST.
- Next: check the barrel jack / cable and the charger rating (NVIDIA 19 V);
  power-cycle; put it back to 15 W (`sudo nvpmodel -m 0`; it was switched to
  25 W at 06:52 today); watch it idle for 10 min before any load.
- Nothing of the robot's was changed: `orin-nav:1.1` and `~/rover` untouched,
  everything committed and pushed. The throwaway container `isaac46_test` was
  removed; the last plain one was `--rm`.

### 2. ROS 2 Lyrical on this Pi 5 -- DONE 2026-10-25 (branch `lyrical-move`)
- Why: Jazzy here vs Lyrical on the Jetson left the brain blind (the Jetson's `image_bridge`
  never matched our readers in the live graph; Jetson OPEN_ISSUES.md 2026-10-25).
- Now: every service on `/opt/ros/lyrical` through `scripts/ros_env.sh`; workspace built by
  `scripts/build_ws.sh` into build/ install/. Brain gets 42 frames/10 s.
- Jazzy removed the same night (owner: "remove Jazzy ... all clean and clear"): the 204
  ros-jazzy packages, its builds, and ~/microros_ws (the old ROS-wrapped agent, unused since
  2026-07-16; the standalone agent's recipe is scripts/build_xrce_agent.sh).
- Next machines: the two laptops (owner: "first the Pi 5 and the Jetson, then the sim").

### 3. Owner-only steps
- GitHub: rename `mitra_sim` -> `kino_sim` (Settings); then the remotes get updated.
- Jetson power mode: decide 15 W / 25 W / MAXN_SUPER once the supply is stable
  (default in nvpmodel.conf is 25 W; it was at 15 W for a long time).
- Old laptop: decide whether the old `rover_sim` in `/workspace` goes.

### 4. Robot fixes found on the twin (Jetson `OPEN_ISSUES.md`, 2026-10-18)
- Door clearance: home_real goal 2 passed the 80 cm door frame at 0.7 cm.
- Leaving the shoe-rack corner took ~250 s (nav2 "no path", `tight` timed out,
  `way_back` stuck twice, then `pass_far` got out). Likely the CPU-bound
  planner (Jetson OPEN_ISSUES #1): re-test after the power mode is settled.
- Parity reference: `./sim parity snapshot real` with the real stack up, once.

### 5. Later
- Isaac Sim 6.x / Isaac Lab 3.0: needs Python 3.12, is tested on driver 595
  (595 crashed Isaac Sim 5.1 on this laptop) and lists 16 GB VRAM as minimum
  (we have 6). Try in a separate env after Isaac Lab 3.0 GA; keep 5.1 + 2.3.2.
- RViz on the new laptop (below) is optional: the old laptop is the RViz station.

---

## LATER — RViz on the new laptop (noted 2026-10-18)

The sim laptop is new (user `chotesh`, 192.168.1.9, DHCP) and runs the twin in
**Isaac Sim**: `~/kino_sim` there, branch `isaac` --
README.md / CLAUDE.md / docs/STATUS.md in it. The full twin was verified
2026-10-18 (`TWIN=1 ./rover up`: every gate OK; home_real scenario 4/4 reached).

RViz is NOT set up on that laptop yet (no ROS there). To finish it:

1. On the laptop, in a terminal (needs sudo): install ROS 2 Lyrical (`ros-lyrical-desktop`
   from NVIDIA's buildfarm, as the Pi 5's scripts/setup_lyrical_root.sh does) -- the laptop's
   move to Lyrical is the next step after the robot (2026-10-25).
2. Then on the laptop: `cd ~/kino_sim && ./sim rviz` (the twin, domain 42) or
   `./sim rviz real` (the real robot, domain 0). Both fetch the layout from the
   Jetson (`~/rover/mapping/rviz/rover_live.rviz`) every time; the twin window adds
   the true world and the rover's true outline (green). Built, but untested
   until ROS is installed.
3. `TWIN=1 ./rover view` on the Jetson still pushes RViz over ssh to
   `rakhi24@<laptop>` and fails on the new laptop (no ssh server, other user).
   Harmless inside `up`; use `./sim rviz` instead, or decide to install an
   ssh server on the laptop.

(`scripts/fleet.sh twin` no longer needs the laptop over ssh: it finds the
twin on domain 42. Done 2026-10-18.)

---

## Still open from earlier passes (condensed 2026-10-18)

The full write-ups (2026-07 .. 2026-10-02) are in git history (`git log -p TODO.md`).
Resolved and dropped then: the Mac's July "Compute error", the supervisor's KV
slot (the supervisor agent is gone), the STT VAD-silence scare, the Pi5<->Jetson
cable (retested clean 2026-10-07), `--swa-full` (cannot fit the 16 GB Mac; the
per-slot cache PASSES and `LANGROBO_WARM_ALL=1` is on), the discovery server
(removed from the repo 2026-10-18), the `Todo.md`/`TODO.md` case clash.

- **Mac Mini:** give the Pi ssh to it (MAC_MINI_TASKS.md Task 1), then
  speculative decoding with Gemma 4's draft model (Task 5) and `--metrics`.
- **Pi 5:** re-run `./scripts/install_systemd.sh` once (retires the old
  `langrobo-discovery` unit), then `sudo systemctl restart langrobo-brain`.
- **Change the Pi 5 sudo password** (it was typed into a chat on 2026-09-26).
- **Floor-grade the exact moves with the owner watching:** `L:90` x6 each way,
  a sequence, a reach through a gap (goal_exec / reach, rover INTELLIGENCE_PLAN.md §5).
- **Decide: should positions survive a power cycle?** (saved places, the map).
  Everything resets at boot today, by design.
- **Give the PoE injector its own socket** (2026-10-02: it lost power with the
  ESP32's restart and took the camera down).
- Soniox STT/TTS providers were never run against a real key.
- Jetson CPU load (rover OPEN_ISSUES #1, #2): measure once the power supply is stable.
