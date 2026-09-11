# Integration gaps — this brain vs. the rover it actually drives

Written 2026-09-06 after reading both repos side by side
(`pi5_ros2_ws` and `-langrobo_perception-`) as one system.

The two repos are individually sound. Almost every real fault lives at the
**seam** between them: a topic one side publishes and the other never
subscribes to, a frame name that means something different on each machine, a
calibration constant copied from a firmware that has since been rewritten.
That is the class of bug this file exists to track, because neither repo's own
tests can see it — `langrobo_core`'s tests all pass against a StubBridge that
answers every topic perfectly.

**Paths in this file span two repos.** `src/…`, `scripts/…` and the docs are
in `pi5_ros2_ws`; anything under `phase1/`, `phase3/`, `phase4/`, `logs/` or
`./rover` is in `-langrobo_perception-` on the Jetson side.

**How to read the status column:** ✅ fixed in this commit · ⬜ open, needs
hardware or a decision.

---

## 1. Topics this brain publishes into the void

Checked by grepping the rover repo for every topic name `ros2_bridge.py`
touches. These have **no publisher or subscriber on the rover at all** — not a
dead node, not a stopped container: no code anywhere that speaks them.

| topic | direction | what depends on it | status |
|---|---|---|---|
| `/vision/detections_3d` | rover → brain | `approach_object`, `where_is`, `list_known_objects`, `forget_object`, `scan_surroundings`'s object report, **the entire `world_model` service** | ⬜ |
| `/vision/target` + `/vision/target_result` | both ways | `navigate_to_visible_object` (the mono visual-servo fallback) | ⬜ |
| `/servo_pan`, `/servo_tilt`, `/camera/pan_tilt_state` | brain → ESP32 | `point_camera`, `ensure_head_centred`, `approach.py`'s pan sweep | ✅ gated off |
| `/audio/music_cmd`, `/audio/music_state` | both ways | every tool in `tools/music.py` | ⬜ |

### `/vision/detections_3d` is the expensive one

It is the input to `world_model`, which is the robot's entire memory of where
things are. With no publisher:

- `approach_object("chair")` searches (now: one dwell, then a full circle of
  base rotation), finds nothing, falls back to "where it was last seen", finds
  nothing there either, and answers *"I haven't seen one before either, so I
  have nowhere to go."* — for a chair it is looking straight at.
- `where_is` answers the same way for everything.
- `~/.langrobo/world_model.json` never gets a single entry.

Only `approach_described_object` works today, because it does not use
detections at all: it asks the VLM for a pixel and grounds that pixel through
`phase4/nodes/pixel_to_goal.py`. **That is the whole working object-approach
path on this rover right now.** It costs one VLM round-trip (~10–40 s) per
look, against ~0 s for a detection lookup.

To close it, the rover needs a node publishing, on `/vision/detections_3d`:

```json
{"frame": "odom",
 "objects": [{"label": "chair", "x": 1.2, "y": -0.4, "z": 0.3, "conf": 0.8}]}
```

`"frame"` must equal `ROS2Bridge.NAV_FRAME` (see §2) or the brain drops the
message — loudly now, silently before.

### `/servo_pan` — no servos exist

`rover_firmware_v2.ino` declares exactly three subscriptions (`/cmd_vel`,
`/pid_gains`, `/reset_odom`) and the micro-ROS entity caps are why it is that
short. There is no servo code and no mount.

This was not merely useless, it was **slow**: `approach.py`'s search swept
three pan angles at 1.6 s dwell each and `ensure_head_centred` slept 0.8 s —
about 5.6 s of dead air before every `approach_object`, plus 0.8 s in front of
every `navigate_to_pose` and `save_location`, spent commanding hardware that
cannot move. Now gated behind `PAN_TILT_ENABLED` (`LANGROBO_PAN_TILT=1` once
servos are wired), and `point_camera` says it has no mount instead of
reporting a successful move.

While gating it I found the two sides disagreed on which way is left:
`point_camera`'s docstring says `-90 = full left`, the old `fastpath.py`
mapped "look left" to `+60`. That second caller is gone (the fast path was
removed — see ARCHITECTURE_LLD.md §3.1), and with it the disagreement: the
docstring is now the only statement of the convention, and the navigate agent
reads it. Still **confirm the physical direction once a mount exists** — the
docstring is currently unverified against hardware, not merely uncontested.

---

## 2. One frame, three hardcodings ✅

`_nav_worker` and `get_current_pose` both hardcoded `"map"`, written against a
fuller perception stack. This rover's Nav2 runs `global_frame: odom`,
single-session, and nothing publishes a map frame — so goals were
untransformable and pose reads returned `None`, silently, forever. Both were
fixed by hand on 2026-09-06.

**A third one was missed.** `on_detections` dropped any message whose
`"frame"` was not `"map"` — so even once a detector exists, every detection it
publishes in the rover's actual frame would have been thrown away without a
log line.

All three now read `ROS2Bridge.NAV_FRAME` (default `"odom"`,
`LANGROBO_NAV_FRAME` to override), and a frame mismatch on detections is now a
throttled warning instead of silence. The point is not the value — it is that
there is one of it.

---

## 3. Drive calibration was written for a firmware that no longer exists ✅

`tools/movement.py` carried:

```python
_LINEAR_VEL_MS  = 0.28   # "translates to ~93% PWM"
_PHYSICAL_VEL_MS = 0.60  # "actual physical speed at 93% PWM"
```

`rover_firmware_v2.ino` runs a **closed-loop PI controller on measured wheel
velocity in SI m/s** (`pidStep(wL, velL, ...)` against encoder feedback).
`/cmd_vel` is genuinely metres per second; there is no PWM fraction to
translate. The rover's own `OPERATIONS.md` §2 measures it: *0.20 m/s commanded
= 20 cm/s at the camera.*

**Where those numbers came from — confirmed 2026-09-07.** The pi5 repo was
still carrying the *previous* firmware, `rover_firmware.ino`, and it is
open-loop:

```c
#define MAX_LINEAR_VEL  0.30f
#define MAX_ANGULAR_VEL 2.0f
#define WHEEL_BASE      0.15f
float linear_pct  = linearX / MAX_LINEAR_VEL;   // commanded m/s -> PWM fraction
```

So on that firmware `linear.x = 0.28` really did mean `0.28/0.30 = 93 %` duty —
which is exactly what the stale comment said. The constants were not guessed;
they were correct for an L298N chassis with a 15 cm wheelbase that no longer
exists. `angular.z = 2.8` against `MAX_ANGULAR_VEL 2.0` saturated at 100 %
duty, which is also why 2.8 "worked" once and was never revisited.

The v2 firmware replaced all of it: BTS7960 drivers, a 34 cm wheelbase, and a
50 Hz PI loop closing on encoder velocity. Nothing about the old mapping
survived, and nothing updated the brain.

So the distance model divided by a speed **2.1× the real one**:
`move_robot("F:20")` computed `0.20 / 0.60 = 0.33 s` of drive and covered
about **9 cm**. Every fine adjustment the robot has ever made was less than
half the size it reported.

**Turn rate is worse, and is the one thing here you should verify on the robot
before trusting it.** The rover repo measured this chassis twice and the two
results genuinely conflict:

- `OPERATIONS.md` §2 — a 2.0 rad/s pivot puts each wheel at 34 cm/s, **over**
  cuVSLAM's tracking limit: "expect jumps".
- `teleop_web.py` — at 2.0 rad/s (≈47% duty) the four tyres **cannot break
  loose sideways at all**, so a pivot command becomes a forward or backward
  curve. That is how an operator drove 10.7 m of "room loop" inside a 1.8 m
  box on 2026-08-22. Teleop's answer was to raise its pivot to 5.0 rad/s, just
  under the 5.06 rad/s full authority, where it pivots cleanly.

The brain was commanding **2.8 rad/s** — the worst of both: fast enough to
disturb cuVSLAM, too slow to actually pivot. The default is now 5.0, matching
teleop's proven-clean pivot, on the reasoning that a turn which silently
curves is unbounded error while a VO jump at least shows up in `vo_z`.

Every constant is now env-overridable so it can be re-calibrated on the robot
without a rebuild: `LANGROBO_LINEAR_VEL_MS`, `LANGROBO_PHYSICAL_VEL_MS`,
`LANGROBO_ANGULAR_VEL_RS`, `LANGROBO_STEADY_ANGULAR_VEL`,
`LANGROBO_TURN_STARTUP_S`. `logs/calibrate_rotation.py` and `./rover compare`
in the rover repo produce the numbers. Three tests now fail if the commanded
speed and the speed used to compute durations ever disagree again.

⬜ **Still open, and now load-bearing on the rover side.** Nav2's
`velocity_smoother` caps angular at 1.5 rad/s (`phase3/config/nav2.yaml`). If
2.0 rad/s cannot pivot this chassis, 1.5 certainly cannot — so Nav2's in-place
rotations are likely curving too, which would explain heading error that
survives a good position fix.

**2026-09-11 — rover repo TODO 40 restored the Spin recovery**, and Spin now
commands 1.5 rad/s: the same suspect number. The two repos' measurements still
conflict and the conflict is exactly the open question here:

| source | date | claim at wz ≈ 2.0 |
|---|---|---|
| `phase1/teleop/teleop_web.py` | 2026-08-22 | 47% duty, **cannot break four tyres loose** — a pivot becomes a curve |
| `phase3/config/nav2.yaml` sweep | 2026-08-23 | commanded 2.0 → **0.59 rad/s measured** on `/odom` |

These may both be right. The sweep read **yaw rate off `/odom`, which cannot
tell a pivot from an arc** — a curving turn produces the same yaw rate. And
rover TODO 14 measured a true pivot needing ~93% duty, which is wz ≈ 4.7, not
2.0. If that reading is correct then Nav2 has never pivoted, only curved, and:

- Spin at 1.5 rad/s (25% duty/wheel) will **translate while it turns** — worse
  than no Spin in the narrow gaps TODO 40 is trying to get through.
- It is a candidate cause of rover **TODO 37** (rotation-induced x,y drift,
  still unverified) and of the timed-turn error in **TODO 36**.

**The one measurement that settles it** — command a pure rotation through Nav2
and log `/odom` x,y as well as yaw. Yaw alone proves nothing. This is test 2 of
rover TODO 40, and until it runs, treat the Spin recovery as unproven.

---

## 4. Voice ✅ / ⬜

Fixed:

- **The wake word ate the start of your command.** On detection the node
  called `_reset_capture()`, which cleared the pre-roll ring as well as the
  capture buffer — so the first utterance after "hey jarvis" had no ~300 ms
  pre-pad and lost its opening syllables. The ring is kept now; the detector
  fires at the *end* of the wake word, so what the ring holds is the run-in to
  the command, which is exactly what you want.
- **The noise gate default was still the broken one.** `vad_gate.GateConfig`
  was corrected to `min_rms=0.05` on 2026-09-05 (the Bluetooth mic's measured
  noise floor is ~0.029), but `stt_node`'s `min_utterance_rms` parameter
  default still declared `0.012` — and a parameter default *overrides* the
  dataclass default. Anyone running the node without `voice_params.yaml` still
  got the gate that lets an idle room through to a cloud recogniser that
  answers noise with confident invented sentences.
- **A barge-in could race the audio device.** `_on_stop` runs on the ROS spin
  thread and called `_close_stream()`; `_play` runs on the playback thread and
  writes to that same stream. Closing a PortAudio stream under an in-flight
  write is a native-level race, reachable by any "stop" landing mid-sentence.
  Now serialised per 100 ms chunk — the granularity `_play` had already chosen
  for stop latency, so nothing got slower.

⬜ **The dominant voice latency is unfixed and structural.** Local Kokoro
measures RTF ~1.8 on this Pi 5: a 3-second sentence takes 5.4 s to synthesise,
and `_synth_loop` synthesises a *whole sentence* before the player gets a
single sample. Sentence-level pipelining already hides this for sentences 2..N
— it cannot hide it for the first one, which is exactly the one the user is
waiting on. Real options, in order of effort: keep `tts_provider:
sarvam_translate` (already the default, sub-second) and treat local Kokoro
strictly as the offline fallback; or chunk long first sentences at clause
boundaries; or move to a streaming synth. Measure first — `/voice/tts_meta`
already publishes per-sentence `rtf`.

---

## 5. Things worth measuring, not yet changed ⬜

- **Camera QoS.** `/camera/color/image_raw/compressed` is RELIABLE on both
  sides (ROS 2 default) over WiFi. Reliable large messages retransmit and can
  head-of-line block; sensor streams are conventionally BEST_EFFORT. Both
  sides must change together — a best-effort publisher and a reliable
  subscriber is an incompatible-QoS match, which fails **silently**, exactly
  the failure mode that has bitten this project repeatedly. Change both repos
  in one commit or neither.
- **`agent_node` runs a SingleThreadedExecutor** (`rclpy.spin(node)`). Every
  subscription and all five timers serialise on one thread with the image
  callback. Nothing measured is wrong today, but `/voice/user_input` waiting
  behind a 5 Hz JPEG callback is the kind of thing that shows up as
  intermittent voice lag under load. A `MultiThreadedExecutor` with callback
  groups is the standard answer if it ever does.
- **`image_bridge` no longer encodes for nobody** (✅ — it now checks
  `get_subscription_count()` first), but 5 Hz of 896×504 JPEG is still
  ~250–400 KB/s while the brain *is* connected, for a `look()` that fires
  every few minutes at most. A request/response frame grab would cost
  essentially nothing; the topic exists because `look()` reads a cache.
  `approach.py`'s comment already calls it "the 2 Hz look feed" while the node
  publishes at 5 — one of the two is wrong about what was intended.

---

## What I could not check

Neither repo was run. This was read on a laptop with no robot, no Jetson and
no Pi 5 attached; `langrobo_core`'s 182 tests pass here, and nothing else in
either repo is executable off-hardware. Everything above is either read from
the code, or quoted from measurements the repos already record. The turn-rate
default in §3 is the one change that alters physical behaviour on evidence
that conflicts with itself — **drive it on blocks first.**
