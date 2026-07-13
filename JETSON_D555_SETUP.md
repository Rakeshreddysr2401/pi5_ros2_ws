# D555 bring-up — what's already done and what to do when the camera arrives

**Status 2026-07-13: ALL software is deployed on both machines and smoke-tested
camera-less.** The full real-profile perception stack (driver wait-loop,
RTAB-Map, nvblox, Nav2, YOLO detections_3d) launches cleanly in the Jetson's
`isaac_ros` container; every node idles waiting for camera data exactly as
designed. What remains is physical: mount + network the camera, measure the
mount offset, and run the acceptance tests below.

Companion docs: `~/robot/DEPTH_CAMERA.md` on the Jetson (hardware/network
detail, compute-budget decision), `NETWORKING.md` both repos.

---

## 0. Architecture (what was decided and why)

```
D555 (Ethernet/DDS, depth computed ON-CAMERA, built-in IMU)
  │ jumbo-frame switch (MTU 9000)
  ▼ isaac_ros container — THE ONLY Jetson container in rover mode
  ├─ realsense2_camera 4.58 (librealsense 2.58) → /camera0/* topics
  ├─ RTAB-Map rgbd_odometry (CPU)  → odom→base_link TF
  ├─ RTAB-Map slam (CPU, 1 Hz)     → map→odom TF + PERSISTENT /data/rtabmap.db
  ├─ nvblox (GPU)                  → 3D reconstruction + Nav2 costmap slice
  ├─ Nav2 (DiffDrive MPPI)         → /cmd_vel (plain Twist → ESP32 wheels)
  └─ detections_3d (YOLOv8n GPU, 5 Hz) → /vision/detections_3d JSON (map frame)
                                       + /camera/color/image_raw/compressed (look())
```

- **No cuVSLAM — decided 2026-07-13.** NVIDIA's JetPack-7 (`noble-jetpack`)
  Isaac ROS debs ship `libcuvslam.so` compiled with **SVE2 instructions**
  (gdb showed `whilewr p0.s` at the fault) for Thor's ARMv9 cores; the Orin
  Nano's Cortex-A78AE (ARMv8.2) has no SVE, so the library SIGILLs on load —
  verified on releases 4.3, 4.4 AND 4.5, in a pristine container. No install
  method fixes a missing CPU extension. Localization is RTAB-Map on CPU;
  reconstruction stays nvblox on GPU. When a new Isaac ROS release appears,
  re-check in 5 s (after apt-upgrading the deb in a throwaway container):
  `python3 -c 'import ctypes; ctypes.CDLL("/opt/ros/jazzy/lib/libcuvslam.so")'`
  — if it stops dying, cuVSLAM can replace RTAB-Map by swapping the
  rgbd_odometry+rtabmap nodes in perception.launch.py for a VisualSlamNode
  composable (base_frame:=base_link, stereo infra + IMU remaps — see git
  history of this file / the launch for the exact block that was removed).
  Nothing else changes: Nav2, nvblox and the brain only consume TF.
- **RTAB-Map upgrade for goal 2**: its map database persists across reboots,
  so `save_location` poses stay valid day to day (cuVSLAM VIO never offered
  that). Map first (`localization:=False`, the default), then flip the launch
  arg to `True` for daily use.
- **Voice is OFF on the Jetson in rover mode** (compute decision in
  DEPTH_CAMERA.md): perception owns the 8 GB. Talk to the robot via Telegram.
  detections_3d publishes the compressed color feed, so the brain's `look()`
  and watch-mode photos still work without ai_stack.
- **Container image**: `isaac_ros:langrobo-nav-stack-1.2` (committed
  2026-07-13; compose points at it). Contents added over `-1.1`→`-1.2`:
  librealsense 2.58.1 + realsense-ros 4.58.1 + isaac-ros-realsense,
  torch 2.12 CUDA (verified `cuda: True`, YOLOv8n 35 ms/frame),
  ultralytics 8.4.80, rtabmap-odom + rtabmap-slam,
  libcusparselt/nvshmem (+`/etc/ld.so.conf.d/cusparselt.conf`).

## 0.5 End-to-end flow — from a command (or a camera frame) to wheels and back

### A. The perception loop (runs continuously, Jetson isaac_ros container)

```
            RealSense D555 (Ethernet/DDS, MTU 9000)
            color 30Hz ── aligned depth 30Hz ── raw depth 30Hz ── IMU (unused v1)
                │                │                  │
                ├────────────────┤                  │
                ▼                ▼                  ▼
        rgbd_odometry (CPU)  detections_3d (GPU)  nvblox (GPU)
        feature-match RGB    YOLOv8n 5Hz → box    integrates depth
        + metric from depth  + depth @ box centre along TF camera pose
                │            + TF → map point         │
                ▼                │                    ▼
        odom→base_link TF        │            3D voxel map + 2D ESDF
                │                │            costmap slice
                ▼                │                    │
        rtabmap SLAM (1Hz)       │                    ▼
        loop closure +           │            Nav2 global/local costmaps
        /data/rtabmap.db ────────┼──────────  (obstacle avoidance)
                │                │
                ▼                ▼
        map→odom TF     /vision/detections_3d ──► Pi5 brain cache
                        {"frame":"map","objects":  (label → x,y,z,conf,
                         [{"label":"chair",...}]}   receive-time age)
                                 │
                                 └─► /camera/color/image_raw/compressed
                                     (2Hz JPEG — brain look() + watch photos)
```

So at any instant the brain knows: WHERE the robot is (TF via get_current_pose),
WHAT is around it and where in the map (detections cache), and Nav2 knows what
space is drivable (nvblox costmap). All three come from the one camera.

### B. A command's journey — "go near the chair" (voice or Telegram)

```
 user speech ──► Jetson STT ──► /voice/user_input ─┐        (voice mode)
 Telegram msg ──► brain telegram poller ───────────┤        (rover default)
                                                   ▼
                                    Pi5 agent_node._process()
                                                   │
                            ┌──────────────────────┴────────────────────────┐
                            ▼ exact movement phrase                          ▼ anything else
                   FASTPATH (fastpath.py)                         LangGraph (supervisor →
                   regex match, ZERO LLM calls                    navigate agent, LLM on
                   <100ms to first spoken ack                     Mac Mini picks the tool)
                            │                                                │
                            └──────────────────────┬────────────────────────┘
                                                   ▼
                                     approach_object("chair")
                                                   │
                              fresh detection in cache (<3s old)?
                                    │yes                    │no — SEARCH:
                                    │             1. pan head -55°/0°/+55°
                                    │                (servos GPIO18/19; base
                                    │                pose ignored while panned)
                                    │             2. still nothing → rotate base
                                    │                60° steps, full circle
                                    │             3. still nothing → last-seen
                                    │                position from world model
                                    ▼
                    re-centre head + settle 0.8s (ensure_head_centred —
                    base pose is trustworthy again)
                                    ▼
                    goal = chair_xy − 0.65m along robot→chair line,
                    yaw facing chair   (0.9m for a person)
                                    ▼
                    Nav2 NavigateToPose action (map frame, async)
                       planner (NavFn on nvblox costmap)
                       controller (MPPI DiffDrive, 20Hz)
                                    ▼
                    /cmd_vel (plain Twist) ──► Pi5 micro-ROS agent ──► ESP32
                                    │              (UDP 8888)          L298N wheels
                    RTAB-Map sees the motion ──► TF updates ──► MPPI corrects
                    (visual odometry IS the wheel feedback — no encoders)
                                    ▼
                    goal reached / failed ──► brain _on_nav_done()
                                    ▼
                    "[SYSTEM] Navigation succeeded: I've arrived near the chair."
                                    ▼
                    spoken via TTS (voice mode) or sent to the Telegram chat
                    that asked (requester routing) — same path reports failures
                    honestly ("path blocked", "couldn't find the chair")
```

Interrupts at any point: a new utterance (or "stop") cancels the Nav2 goal and
sets the motion interrupt — every loop above checks it and halts the wheels.

### C. "What do you see?" (picture path, no movement)

```
question ──► brain (LLM route: local_agent) ──► look() tool
   ──► latest /camera/color/image_raw/compressed frame (≤10s old, else
       "I can't see right now") ──► image into the multimodal LLM turn
   ──► spoken/texted description; frame stays in history for follow-ups
```



| Topic / action | Type | Direction | Purpose |
|---|---|---|---|
| `/vision/detections_3d` | String (JSON) | Jetson → Pi5 | map-frame objects `{"frame":"map","objects":[{"label","x","y","z","conf"}]}` — non-map frames are DROPPED by the brain |
| `/camera/color/image_raw/compressed` | CompressedImage | Jetson → Pi5 | look() + watch photos (2 Hz, from detections_3d) |
| `/camera/pan_tilt_state` | String (JSON) | Pi5 → Jetson | commanded head angles — diagnostics ONLY, never TF (§3) |
| `/servo_pan`, `/servo_tilt` | UInt16 | Pi5 → ESP32 | raw servo angles (0-180, 90 = centre; GPIO 18/19) |
| `/navigate_to_pose` | NavigateToPose action | Pi5 → Jetson Nav2 | goals in `map` (named places AND approach_object standoffs) |
| `/cmd_vel` | Twist (plain) | Nav2 → ESP32 | wheels; the TwistStamped stamper is sim-only |

## 2. Physical hookup (the only remaining work)

1. **Servos**: pan → ESP32 GPIO 18, tilt → GPIO 19, power from the 5 V rail
   (never the 3V3 pin), common ground. Flash the updated
   `ESP_32_frimware/rover_firmware.ino` (dual servo, centres on boot, tilt
   clamped 60–120 in firmware). Verify from the Pi5:
   ```bash
   ros2 topic pub -1 /servo_pan std_msgs/UInt16 "{data: 150}"   # head left
   ros2 topic pub -1 /servo_pan std_msgs/UInt16 "{data: 90}"    # centre
   ```
2. **Camera network** (D555 is Ethernet-ONLY; USB-C is power only —
   USB-Ethernet adapters do NOT work):
   - Through the **jumbo-capable switch** (MTU 9000 end-to-end; the
     JioAirFiber router drops jumbo — tested). Direct D555→Jetson ethernet
     works for a first test but steals the Pi5 link.
   - `sudo ip link set enP8p1s0 mtu 9000` on the Jetson.
   - Static IP on the camera: `docker exec -it isaac_ros bash` →
     `source /opt/ros/jazzy/setup.bash && rs-eth-config` → set
     **192.168.2.30/24** (tool verified present, v2.58.1).
   - See it: `rs-enumerate-devices` inside the container lists the D555.
3. **Mount**: fix the D555 on the pan/tilt bracket, servos centred. Measure
   base_link→camera (forward X, left Y, up Z, pitch) — passed as launch args.

## 3. TF: the camera is on a servo head — the chain stays RIGID

Same reasoning as before, now with RTAB-Map in the vSLAM seat: the visual
odometry tracks the CAMERA. When the head pans by θ, `map→camera` stays
correct (that's what odometry measures) while `map→base_link` reads rotated
by θ. Detections and nvblox integration consume `map→camera` — so objects
spotted mid-sweep land at their TRUE map position and the 3D map built while
panning is correct (requirement goal 4). Publishing the servo joint into TF
as well would count the rotation twice. Therefore:

- `base_link→camera0_link` is ONE static transform, measured at pan=0/tilt=0
  (launch args `cam_x/cam_y/cam_z/cam_pitch`).
- The brain never drives while panned and re-centres + settles ~0.8 s before
  reading its pose or sending goals (`ensure_head_centred` in
  tools/movement.py) — so goal computation always uses a correct base pose.

Geometry acceptance test: chair ~2 m ahead; compare its `/vision/detections_3d`
x/y with head centred vs panned 55° (`/servo_pan 145`) — must agree ±0.15 m.

## 4. Run it

```bash
# from the Pi5 — brings up EVERYTHING (micro-ROS wheels + Jetson perception):
./scripts/fleet.sh rover

# Jetson-side by hand, if needed:
~/robot/scripts/fleet_role.sh perception start real   # or stop / status
# logs:
tail -f ~/robot/data/perception_launch.log
```

Launch args (edit `run_perception_real.sh` or pass through):
`cam_x:=… cam_z:=… cam_pitch:=…` (measured mount), `localization:=True`
(after the house is mapped), `run_nav2:=False` (debug perception alone).

## 5. Bring-up order + verification (camera day)

1. Servos (§2.1). 2. Camera network (§2.2) — `rs-enumerate-devices` sees it.
3. `./scripts/fleet.sh rover`, then inside the container:
   ```bash
   ros2 topic hz /camera0/color/image_raw            # ~30 Hz
   ros2 topic hz /camera0/aligned_depth_to_color/image_raw
   ros2 topic hz /camera0/depth/color/points         # collision monitor source
   ros2 run tf2_ros tf2_echo map base_link           # pose after ~10 s (RTAB-Map up)
   ```
4. **Map the house**: drive around by voice/Telegram ("forward 50", "turn
   left") or say "scan the room" in each room. RTAB-Map grows /data/rtabmap.db;
   nvblox fills the costmap.
5. From the Pi5: `ros2 topic echo /vision/detections_3d --once` → chair/person
   JSON in map coordinates.
6. Spoken/Telegram acceptance tests, increasing machinery:
   1. "look left" → head pans (servos + firmware)
   2. "go near the chair" → instant ack (fast-path, zero LLM), Nav2 drive,
      parks 0.65 m away, "[SYSTEM] I've arrived near the chair"
   3. stand BEHIND the robot, "come here" → head sweep → base rotation →
      finds you → parks 0.9 m in front
   4. "save this location as dining area" … "go to the dining area"
   5. reboot everything, "go to the dining area" again — persistence proof
   6. "scan the room" → slow full circle, map fills behind/left/right
7. `python3 scripts/latency_replay.py "come here"` — command-to-ack must be
   LLM-free (fast-path stage only).

## 6. Watch-outs

- **Memory**: rgbd_odometry + rtabmap + nvblox + YOLO + Nav2 ≈ 4–5 GB with
  the 848×480 profile. `sudo tegrastats` on first run; if RAM >90 %, drop the
  camera profile to 640×360 in perception.launch.py first.
- **Collision monitor**: watches `/camera0/depth/color/points`. If that topic
  isn't flowing, Nav2 will HOLD the robot (source timeout) — that's the "robot
  won't move but planning works" symptom. Verify step 3 covers it.
- **Wheel calibration**: the ESP32 is open-loop (commanded m/s ≠ physical
  m/s, PWM map in firmware). Nav2 speeds are capped conservatively
  (vx ≤ 0.25) and RTAB-Map odometry closes the loop, but don't tune MPPI
  critics until wheels are calibrated against real odometry.
- **DS-mode CLI caveat**: `ros2 node list` inside containers is unreliable
  with the Discovery Server — verify by `ros2 topic echo` on real topics.
- The XMLPARSER "realpath failed" line on every node start is the blanked
  `FASTRTPS_DEFAULT_PROFILES_FILE` — cosmetic, ignore.
- **ai_stack** is parked in rover mode. Voice hardware work (STT/TTS on Pi5
  or time-sharing) is a separate later phase; Telegram is the interface.
  Note: compose pins `ai_stack:dev-1.0.0` while `dev-1.0.6` exists — decide
  which is canonical before the next voice session.

## 7. What was deployed where (2026-07-13)

Jetson `~/workspaces/isaac_ros-dev/src/langrobo_perception/` (built,
`--symlink-install`):
- `langrobo_perception/detections_3d_node.py` — YOLO + depth → map-frame JSON
  + compressed look() feed (no cv_bridge/tf2_geometry_msgs deps by design)
- `launch/perception.launch.py` — `mode:=real` profile (driver + RTAB-Map +
  nvblox + Nav2 + detections_3d); sim profile untouched
- `config/nvblox_real.yaml`, `config/nav2_real.yaml` (DiffDrive, wall clock,
  map goals, ESP32 speed envelope)
- `scripts/run_perception_real.sh`

Jetson `~/robot/`: `scripts/fleet_role.sh` (NEW — was missing; fleet.sh had
been calling it into the void), `docker-compose.yml` → image
`isaac_ros:langrobo-nav-stack-1.2` (backup of original at
docker-compose.yml.bak-20260712).

Pi5 (this repo): `scripts/fleet.sh` rover mode = micro-ROS + Jetson
perception-real (voice→Telegram); brain tools/fastpath/bridge/firmware as per
git diff; `ensure_head_centred` discipline in tools/movement.py.
