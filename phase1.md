# Phase 1 — "Drive me around once, then know my home"

**Goal (agreed 2026-07-19):** the rover can be tele-driven around the whole home from a
phone, builds a map that **survives reboot**, relocalizes **from anywhere** (no fixed
dock), remembers **named places** taught over Telegram ("this is the TV" → later
"go near the TV"), approaches people/objects in **unmapped** spaces, and does all of it
**without hitting anything** — on current hardware (encoders are a separate hardware
track, ThingsToDo #9; nothing in phase 1 waits for them).

**Why this is possible now:** verified 2026-07-19 — the installed pyCuVSLAM cu12 wheel
exposes the full SLAM API (`Tracker.SlamConfig`, `save_map`, `localize_in_map`,
loop-closure poses, pose graph). Our `cuvslam_ros_node.py` currently uses odometry-only.
Persistence is a wrapper upgrade, not a new SLAM stack.

**Where the code goes**
- Jetson (`langrobo_perception/orin-nav-stack`): SLAM mode, map save/load services,
  pose-sanity watchdog, virtual bumper.
- Pi5 (`pi5_ros2_ws`): web teleop, places store + tools, Telegram flows.

---

## Step 0 — Prerequisites (half a day, mostly hands)

1. **USB-flash the committed ESP32 firmware** (`e282a13`: reconnect state machine,
   PWM stall-floor, WiFi sleep off). Fill WiFi/OTA passwords locally first.
2. After flash, verify: agent restart → ESP32 re-establishes session on its own
   (no rover power-cycle ritual); then **delete `cmd_vel_deadband.py` rescaling**
   (keep the speed *caps* — move them into the teleop node and nav2 params).
3. Re-verify e-stop paths from the README §8 (stop, down, container-down zero-vel).

*Acceptance:* kill and restart `langrobo-microros` → `/cmd_vel` subscriber returns
within 30 s with no human touch.

## Step 1 — Web joystick teleop (Pi5, ~2 days)

A phone-browser page served by the Pi5 (alongside the existing health API):

- **UI**: virtual joystick + arrow buttons, speed slider (hard-capped vx ≤ 0.22,
  wz ≤ 0.90), big red STOP, live camera view (MJPEG re-serve of the 2 Hz look feed).
- **Transport**: WebSocket; page sends stick state at 15 Hz; server publishes
  `/cmd_vel`.
- **Deadman everywhere**: release stick → zero; WebSocket silent > 300 ms → zero;
  page closed → zero; plus the ESP32 500 ms watchdog underneath. Four layers.
- **Single-driver rule**: while a teleop client is connected, brain movement/nav
  tools return "under manual control" (shared lock topic `/control_mode`).
- New node `langrobo_ros/teleop_web.py` + `static/drive.html`; systemd unit
  `langrobo-teleop.service` (manual start, not boot-enabled in phase 1).

*Acceptance:* drive every room from the phone; WiFi-off mid-drive stops the rover
< 0.5 s; brain refuses "go forward" while the page is driving.

## Step 2 — Persistent map: cuVSLAM SLAM mode (Jetson, ~3 days, the core)

Upgrade `cuvslam_ros_node.py`:

1. **Enable SLAM** (`SlamConfig` + loop closure) instead of odometry-only. Publish
   map→odom correction TF (today it's a static identity); nav2 already runs in map
   frame — no nav2 changes.
2. **Services**: `/slam/save_map` (→ `/data/maps/<map_id>/`), `/slam/load_and_localize`
   (load + `localize_in_map`), `/slam/status` (localized? loop closures? map_id).
   `run_stack.sh map-save | map-load` wrappers.
3. **Relocalize-from-anywhere behavior** ("must start anywhere" chosen): on
   `load_and_localize`, if not converged from the standing view, do **short supervised
   rotation pulses** (wz floor is 0.8 rad/s — pulse 300 ms, pause, retry localization,
   max one full turn). ⚠ Tether rule applies: phase-1 relocalization spins happen only
   with the operator present; the brain announces "looking around to find myself" on
   Telegram first. If still lost after one turn → report "I can't recognize this place"
   and stay put (Telegram hint "you're in the bedroom" can seed retry — cheap to add,
   already allowed by the design).
4. **Scale honesty (interim)**: apply the measured ×1.2 translation correction inside
   the wrapper so pose, map, and goals agree with reality (`drive_test` CAL then drops
   to 1.0 — one source of truth). Proper D555 stereo recalibration stays phase 2
   (ThingsToDo #9) — until then keep standoffs ≥ 0.45 m.
5. **Map lifecycle**: `remap` keeps meaning "fresh throwaway map"; `map-save` promotes
   the current session to `/data/maps/home_v<N>`; exactly one "active" map symlink.

*Acceptance:* teleop-map the home → `map-save` → full stack restart → place the rover
in 3 different rooms → each time `load_and_localize` converges and reported pose is
within ~0.3 m of a tape-marked spot. Loop-closure check: drive a full loop, pose error
at return < 0.2 m.

## Step 3 — Costmap on top of a loaded map (Jetson, ~1 day)

nvblox stays live/rebuilding (obstacles move; that's fine — this is also what makes
unknown-space work). After successful relocalization, the brain performs one **seeding
look-around** (two short rotation pulses) so the local costmap isn't empty before the
first goal. `allow_unknown: true` stays. No nvblox persistence in phase 1 (revisit in
phase 2 only if goals into unseen rooms prove unreliable).

## Step 4 — Named places over Telegram (Pi5, ~2 days)

- **Store**: `~/ros2_ws/data/places.json` — `name → {map_id, pose(x,y,yaw), taught_at,
  photo_thumb}`. Places are **bound to the map_id** they were taught in; loading a
  different/fresh map ⇒ brain answers "I know 'TV' but not on this map — re-teach me."
- **Tools** (`langrobo_core/tools/places.py`): `save_place(name)` (current map-frame
  pose + camera snapshot), `go_to_place(name)` (NavigateToPose to stored pose with the
  standard standoff; reuses the existing zero-stamp goal path), `list_places()`,
  `forget_place(name)`.
- **Telegram UX (fastpath additions, no LLM in the loop)**:
  - "this is the TV" / "save this spot as TV" → save + reply with the snapshot.
  - "go near the TV" → known → drive (arrival report already exists in fastpath);
    unknown → "I don't know where 'TV' is yet — drive me there and tell me."
  - "where do you know" → list.
- Object-teaching variant: "the chair in front of you" → save the *object's* map
  position from `/vision/detections_3d` if YOLO sees it, else the robot's own pose;
  if it can't see it, it says so ("not able" honesty requirement).

*Acceptance:* teach 5 places, reboot the whole stack, relocalize, "go near the TV"
from a different room works; a never-taught name gets the honest answer; a fresh map
gets the re-teach answer.

## Step 5 — Unknown-space person/object approach (polish, ~2 days)

Machinery exists (`visual_approach.py`, `pixel_to_goal`, on-the-fly nvblox). Phase-1
hardening:

- Wrap as a brain tool with an **envelope**: max travel 4 m, timeout 60 s, abort on
  target lost > 3 s (then say so), abort on pose-sanity trip (Step 6).
- YOLO **stays on** during approach (it *is* the sensor) but at the reduced
  `idle_detect_rate` profile while wheels move — CPU headroom is the known cuVSLAM
  killer, so RViz/foxglove must be off; the tool checks that before moving.
- Person standoff 0.8 m (existing env var), objects 0.45 m.

*Acceptance:* in a room the map has never seen: "go near the person" → stops at
standoff, no contact, honest failure when the person walks out of view.

## Step 6 — No-crash layer (Jetson, ~2 days, runs under everything)

1. **Pose-sanity watchdog node** (new, tiny): monitors `/odom` — translation jump
   > 0.5 m between consecutive updates, z drifting > 0.3 m, or costmap out-of-bounds
   warnings ⇒ publish zero `/cmd_vel`, cancel nav goals, alert on Telegram
   ("stopped: my position went crazy — say 'remap' to reset"). This turns the
   2026-07-19 pose-explosion class of failure from dangerous into a clean stop.
2. **Virtual bumper**: consume nvblox ESDF in front of the footprint — obstacle
   < 0.15 m in the direction of travel ⇒ veto forward `/cmd_vel` (teleop AND nav).
   The camera's 0.4 m blind zone means this fires *before* entering it, never inside.
3. Nav2 conservative profile stays (caps, safe BT, no recoveries). Post-goal
   **pose-settle check** (10 s flat before YOLO restart / next goal) becomes code in
   the goal tool, not an operator rule.

*Acceptance (fault injection):* cover the camera mid-goal → clean stop + alert;
kill cuvslam node mid-goal → clean stop; drive teleop straight at a wall → bumper
stops it at ~0.15 m despite the stick held forward.

## Step 7 — Integration demo + docs (~1 day)

Full scenario, one take: flash-fresh boot → phone teleop around the home → `map-save`
→ teach TV/sofa/kitchen → reboot → rover placed in a random room → relocalizes →
Telegram "go near the TV" → arrives → "go near the person" in an unmapped room →
arrives at 0.8 m. Update `orin-nav-stack/README.md` + `OPERATIONS.md` + `TELEGRAM.md`,
push both repos.

---

## Order & rough effort (evenings/sessions, sequential ~2 weeks calendar)

| # | Step | Effort | Depends on |
|---|---|---|---|
| 0 | ESP32 flash + shim removal | 0.5 d | USB access |
| 1 | Web teleop | 2 d | 0 |
| 2 | cuVSLAM SLAM persistence | 3 d | — (parallel with 1) |
| 3 | Costmap seeding | 1 d | 2 |
| 4 | Named places + Telegram | 2 d | 2 (needs map frame) |
| 5 | Unknown-space approach polish | 2 d | 6 recommended first |
| 6 | No-crash layer | 2 d | — (parallel, do early) |
| 7 | Demo + docs | 1 d | all |

Suggested build order: **0 → 6 → 1 → 2 → 3 → 4 → 5 → 7** (safety net before the fun).

## Risks / honest limits in phase 1

- **Scale patch, not calibration**: ×1.2 correction is a measured constant; walls in
  the costmap are still only as good as D555 factory stereo. Standoffs stay generous
  until the phase-2 recalibration.
- **Rotation floor (0.8 rad/s)**: relocalization/seeding turns are pulsed, not smooth —
  tether supervision required for any spin until encoders enable slow turns.
- **CPU ceiling**: SLAM mode + loop closure costs more than odometry — if cuVSLAM
  drops below ~25 FPS with YOLO idle, we trade nvblox voxel size or YOLO idle rate.
  Measure at Step 2 before building on top.
- **cuVSLAM `localize_in_map` quality** is unproven on this robot — Step 2 acceptance
  is the go/no-go gate; if it can't relocalize reliably, fallback is the manual-hint
  flow (Telegram "you're in the bedroom") which the design already includes.

## Phase 2 preview (not now)

Encoders + PID + wheel-odom EKF fusion (ThingsToDo #9) → true slow creep, recovery
behaviors unlocked; D555 stereo recalibration; ToF ring; place migration between map
versions; vision fastpath (cut the ~60 s vision turn); map viewer on the teleop page.

## Progress log

**2026-07-19 (late night)** — Steps 2 (core) + 6 implemented on the Jetson (commit `1617c84`,
branch `dev_0.0.2_cuVslam_nav`):
- cuVSLAM now runs FULL SLAM (planar constraints, loop closure, pose graph): live map→odom
  TF replaces the static bridge; `/slam/save_map` + `/slam/localize` services; `/slam/status`
  1 Hz JSON; maps persist on the Jetson at `~/orin-nav-stack/maps/`.
- No-crash layer live: `safety_guard.py` = pose-sanity watchdog (jump/z/tilt → cancel nav
  goals + zero wheels, auto-clear 10 s) + nvblox virtual bumper (blocks forward only).
  Wheel chain: `cmd_vel_nav → deadband → /cmd_vel_shim → guard → /cmd_vel`.
- Laptop RViz live view working: run `~/rover_view.sh` on the laptop (192.168.1.12).
- Verified stationary only. NEXT (needs operator): teleop mapping run → save a real map →
  relocalize test (stationary-map relocalize correctly refuses: too few keyframes).
  Then Step 0 ESP32 flash, Steps 1/3/4/5.

**2026-07-19 (first live mapping run, operator present)** — jetson commit `9777fa2`:
- SLAM tuning: `async_sba=True` + `lc_throttle_ms=2000` (now defaults). Before: 2 pose
  explosions (fast straight bursts / loop-closure revisit CPU spikes). After: rotations
  mm-accurate (out-and-back → 0.000/−0.001 m), loop closures firing + correcting live.
- **safety_guard validated in real failures**: caught both explosions (0.41 m jump; |z| 0.55 m),
  cancelled goals, zeroed wheels, auto-cleared. The no-crash layer works.
- **Translation mapping blocked by hardware**: motors stall or breakaway ~0.5 m/s (nothing
  between; threshold rises as battery sags — by end of session cmd 0.30 stalled). ~0.5 m/s
  real speed explodes tracking. → Step 0 (ESP32 flash) is now the critical path; charge
  battery before next run. Mapping recipe that works: nav2 OFF during teleop-map, short
  bursts, rotations freely.
- Laptop RViz: works but laptop must be logged into the desktop; `~/rover_view.sh`.
- New Claude skills on the Jetson: `/rover-start`, `/rover-stop` (full cross-machine runbooks).
