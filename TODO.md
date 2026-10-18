# TODO — pending on-device work

## NOW — open items, 2026-10-18 (most urgent first)

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

### 2. Isaac ROS 4.6 — `orin-nav:1.2` is THE image (owner, 2026-10-18); step F = first real-robot run
- **Status 2026-10-18 evening** (rover `c530fbe`, ISAAC_ROS_4_6_UPGRADE.md §5-6):
  `orin-nav:1.2` (1.1 + Isaac ROS 4.6, cuVSLAM as `VO=isaac`) is built, and
  `./rover` defaults to it for the robot and the twin (owner: "go ahead with
  1.2"). Twin: every gate OK, `home_real` 4/4. Step F, the first real-robot
  run with the owner watching, then delete 1.1; until then
  `IMAGE=orin-nav:1.1 ./rover up` is the rollback.
- The board's stability gate (§0 / item 1) is still open: the boot before
  21:37 ended at 18:17 with no shutdown and no kernel error — the same
  power-cut signature.
- 4.6.0 (2026-08-18) adds official Jetson Orin + JetPack 7.2 support and builds
  cuVSLAM from source for aarch64, so the packaged `isaac_ros_visual_slam` may
  finally run on the Orin and could replace the pyCuVSLAM wheel in `vo_node`.
  Stays on ROS 2 Jazzy. (5.0.0 moves to ROS 2 Lyrical: not now.)
- Plan, never touching `orin-nav:1.1`: in a throwaway container switch apt to
  `isaac-ros/release-4.6`; run the packaged visual SLAM against the twin's D555
  (does it start on the Orin Nano? landmarks vs vo_node? CPU vs vo_node?);
  if it wins, build `orin-nav:1.2`, run the full twin stack + `./sim scenario
  home_real`, then the real robot with `IMAGE=orin-nav:1.2 ./rover up`.
- The 4.5/4.6 notes fix none of our nvblox issues (specks, < 3 cm objects,
  odom-frame drift): those are config/sensor work.

### 3. Owner-only steps
- GitHub: rename `mitra_sim` -> `kino_sim` (Settings); then the remotes get updated.
- Mac Mini, once: `scripts/mac_mini/migrate_to_kino.sh` (until then, do not
  `git pull` there, or `e12b5` stops). Then delete that script.
- Pi 5: `sudo systemctl restart langrobo-brain` (still on 2026-10-17's code;
  voice was restarted on 2026-10-18).
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

1. On the laptop, in a terminal (needs sudo): install `ros-jazzy-desktop`
   (the ROS 2 apt repo for Ubuntu 24.04 "noble", then `sudo apt install ros-jazzy-desktop`).
2. Then on the laptop: `cd ~/kino_sim && ./sim rviz` (the twin, domain 42) or
   `./sim rviz real` (the real robot, domain 0). Both fetch the layout from the
   Jetson (`~/rover/phase2/rviz/rover_live.rviz`) every time; the twin window adds
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
