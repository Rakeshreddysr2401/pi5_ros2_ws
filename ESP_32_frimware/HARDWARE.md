# LangRobo Rover — Hardware & Firmware Reference

Complete record of the drivetrain rebuild (2026-08). Source of truth for wiring,
motor/encoder specs, the ESP32 firmware, flashing, and bring-up testing.
Companion to `LangRobo_Wiring_Documentation_v1.pdf` (the raw pin list).

---

## 0. Current status (bench test 2026-08-07, wheels OFF rover)

**VERIFIED GOOD — ready to standardize / mount to rover:**
- ✅ ESP32 flashes and connects to the micro-ROS agent over WiFi (time sync OK).
- ✅ All 4 BTS7960-driven motors run; 12 V battery + power-bank scheme works.
- ✅ All 4 encoders count (LF, LR, RF, RR) at 3V3 supply — no level shifter needed.
- ✅ Directions correct after flipping the RIGHT side (mirror-mounted): all four
     wheel-tops drive forward together on a `+vx` command. Closed-loop PID holds speed.
- ✅ `/cmd_vel` link works (drove forward/stop from ROS).

**OPEN ITEM (does NOT block mounting or driving):**
- ⚠️ `/wheel_odom` doesn't reach the host yet. The ESP32 publishes it, but
     `nav_msgs/Odometry` (~700 B) is too big for the micro-ROS WiFi transport, so the
     packets drop (the small `/cmd_vel` Twist crosses fine). Fix for the nav-fusion
     phase: ESP32 sends a SMALL message (wheel speeds / tick counts) + a tiny Pi5 relay
     node rebuilds `/wheel_odom` for the EKF. Localization works meanwhile via cuVSLAM
     + gyro, so this is an enhancement, not a blocker.

**VERIFIED direction flags (already set in `rover_firmware_v2.ino`):**
`L_MOTOR_DIR=+1  R_MOTOR_DIR=-1  ENC_LF_DIR=+1  ENC_LR_DIR=+1  ENC_RF_DIR=-1  ENC_RR_DIR=-1`

### Before you STANDARDIZE (permanently mount / solder)
- [ ] Encoder **Blue → 3V3** on all 4 (NOT 5 V/VIN) — this is what keeps GPIOs safe.
- [ ] Common ground tied: battery(−), ESP32 GND, both BTS7960 GNDs, all encoder Blacks.
- [ ] R_IS / L_IS left unconnected on both drivers.
- [ ] Strain-relief the motor + encoder leads (vibration kills solder joints).
- [ ] Keep the ESP32 reachable for USB reflash OR set an OTA password (§8) — OTA is
      currently unauthenticated.
- [ ] Firmware `WIFI_PASS` filled locally; never commit real credentials.
- [ ] Router: reserve the ESP32's DHCP lease so its IP doesn't move.

---

## 1. What the rover is

Differential-drive (skid-steer) rover, 4 driven wheels:

- **Compute:** Jetson Orin (perception/nav) + Raspberry Pi 5 (brain + micro-ROS agent).
- **MCU:** ESP32 DevKit V1 — runs `rover_firmware_v2.ino`, talks micro-ROS over
  **WiFi UDP** to the agent on the Pi5 (`192.168.1.16:8888`). ESP32 IP `192.168.1.11`.
- **Motor drivers:** 2× **BTS7960 (IBT-2)** H-bridges — one per side, each driving
  the two motors of that side in parallel.
- **Motors:** 4× **Rhino GB37 12V geared encoder motors** (see §3).
- **Camera:** Intel D555 (PoE/ethernet DDS camera on the Jetson) — not on the ESP32.
- **Power:** 12V battery → motor drivers; 65W USB power bank → ESP32.

### Data flow
```
/cmd_vel (Twist) ──WiFi──> ESP32 ──PID──> BTS7960 ──> wheels
wheels ──encoders──> ESP32 ──> /wheel_odom (Odometry) ──WiFi──> Jetson EKF
```

---

## 2. Motor driver — BTS7960 (IBT-2), one per side

| BTS7960 pin | Left → ESP32 | Right → ESP32 | Notes |
|---|---|---|---|
| RPWM | GPIO18 | GPIO23 | PWM, forward channel |
| LPWM | GPIO19 | GPIO5  | PWM, reverse channel |
| R_EN | GPIO21 | GPIO27 | driver enable (held HIGH) |
| L_EN | GPIO22 | GPIO13 | driver enable (held HIGH) |
| VCC  | ESP32 5V/VIN | ESP32 5V/VIN | logic supply (5V) |
| GND  | common GND | common GND | |
| B+ / B− | 12V battery + / − | 12V battery + / − | motor power |
| M+ / M− | to that side's motors | to that side's motors | see §4 |
| R_IS / L_IS | **not connected** | **not connected** | current-sense unused |

**Drive scheme:** forward = PWM on RPWM, LPWM=0; reverse = PWM on LPWM, RPWM=0;
both EN held HIGH. (This is different from the old L298N IN1/IN2+ENA scheme.)

> GPIO5 is a boot strapping pin — it idles HIGH via its internal pull-up, and the
> BTS7960 LPWM input is high-impedance, so there's no boot conflict. Don't add a
> pulldown there.

---

## 3. Motors & encoders — Rhino GB37 12V (180 RPM variant)

| Spec | Value |
|---|---|
| Base motor RPM | 5800 |
| No-load output RPM | 200 |
| **Rated output RPM** | **193** |
| Gear ratio | **1 : 30** |
| Rated voltage | 12 V |
| Rated current | 300 mA |
| Rated / stall torque | 0.8 / 3.5 kg·cm |
| Shaft | 6 mm D-type, 15 mm |

### Encoder (quadrature hall)
| Spec | Value |
|---|---|
| Type | Quadrature (2-channel hall) |
| PPR (motor shaft, 1 channel) | 13 |
| CPR (motor shaft, ×4 quad) | 52 |
| **CPR at output/wheel shaft** | **1560** (52 × 30) |
| Encoder supply (datasheet) | 5 V |

### Motor wire colours (per motor, 6 wires)
| Colour | Function |
|---|---|
| **Red** | Motor + (M1) → BTS7960 M+ |
| **White** | Motor − (M2) → BTS7960 M− |
| **Blue** | Encoder Vcc → **ESP32 3V3** (see §5) |
| **Black** | Encoder GND → common GND |
| **Green** | Encoder C1 (channel A) → GPIO |
| **Yellow** | Encoder C2 (channel B) → GPIO |

---

## 4. Motor ↔ driver wiring

- Left BTS7960 **M+** → Left-Front Red + Left-Rear Red
- Left BTS7960 **M−** → Left-Front White + Left-Rear White
- Right BTS7960 **M+** → Right-Front Red + Right-Rear Red
- Right BTS7960 **M−** → Right-Front White + Right-Rear White

Both motors on a side share one driver, so they always get the same drive; their
two encoders are averaged in firmware (§6).

---

## 5. Encoder power — the 3.3 V vs 5 V decision

The GB37 encoder is **rated 5 V**, and a hall encoder outputs pulses at whatever
voltage powers it. If powered at 5 V, the Green/Yellow lines swing 0–5 V — but the
ESP32 GPIOs are **3.3 V only** (34/35/36/39 are input-only with **no** over-voltage
protection). 5 V on them can damage the chip.

**Current build:** encoder **Blue → ESP32 3V3** (not 5 V). Outputs are then 3.3 V →
safe to wire Green/Yellow straight to the GPIOs, no level shifter. The ~15 mA × 4
encoders is well within the 3V3 regulator.

**CONFIRMED (2026-08-07):** all 4 encoders count cleanly at 3V3 — no level shifter
needed. (Fallback if a future encoder is flaky at 3.3 V: move Blue back to 5 V and add
a **5 V→3.3 V divider on every Green/Yellow line** — `1 kΩ` series + `2 kΩ` to GND =
3.33 V, 8 signals = 16 resistors, or two 4-channel BSS138 modules.)

---

## 6. Encoder → GPIO map (all 4 read)

| Wheel | A (Green/C1) | B (Yellow/C2) | Notes |
|---|---|---|---|
| Left-Front | GPIO34 | GPIO35 | input-only pins |
| Left-Rear | GPIO36 (VP) | GPIO39 (VN) | input-only; see erratum below |
| Right-Front | GPIO32 | GPIO33 | |
| Right-Rear | GPIO25 | GPIO26 | |

Firmware reads **all four** and **averages the two encoders per side** → more
resolution, less noise, and it survives one dead encoder. Uses 4 of the ESP32's
8 PCNT hardware quadrature units (via the ESP32Encoder library).

> **GPIO36/39 (Left-Rear) erratum:** these VP/VN pins can register occasional
> phantom pulses on some ESP32s. The front/rear averaging on that side masks it.
> If left-rear counts look noisy in the bench test, add an RC filter (~1 kΩ +
> 100 nF) on those two lines.

---

## 7. Common ground (mandatory)

One common ground node ties together: **battery (−)**, **ESP32 GND**, **both
BTS7960 GNDs**, **all 4 encoder Blacks**. Without a shared ground the encoder
signals are meaningless.

---

## 8. Firmware — flashing & bring-up

Firmware: `firmware/rover_firmware_v2.ino`. It is fully calibrated for this rover
(85 mm wheels, 340 mm track, CPR 1560, max wheel vel 0.86 m/s).

Requires **ESP32 Arduino core 3.x** (LEDC is by-pin: `ledcAttach`/`ledcWrite`; the
firmware already uses the 3.x API). QoS is **BEST_EFFORT** both ways (reliable stalls
over micro-ROS WiFi).

### Flash (first time = USB)
1. Arduino IDE / arduino-cli with the **ESP32** board package (core **3.x**).
2. Install libraries: **micro_ros_arduino** (jazzy branch) + **ESP32Encoder** (madhephaestus).
3. Fill in `WIFI_PASS` locally (placeholder — never commit). OTA password is optional
   (`#define OTA_PASSWORD ...`); leave undefined for bench, SET before deployment.
4. USB-flash. (After this, OTA works: `arduino-cli upload -p rover-esp32.local ...`.)
5. Boot serial should read: `[PWM] attach OK` → `[ENC] 4x quadrature attached` →
   `[uROS] time sync OK` → `[uROS] CONNECTED`, then the 2 Hz `IN … | OUT …` heartbeat.

### Phase 1 — bench test, WHEELS OFF THE GROUND  ✅ DONE 2026-08-07
Serial @115200 prints `IN tgt … | OUT velL/velR odom(...) | enc LF/LR/RF/RR` at ~2 Hz.
1. `ros2 topic info /cmd_vel` → **Subscription count: 1**.
2. **Encoder signs** — roll each wheel forward by hand; its count must go **UP**.
   (Right side counted −ve → `ENC_RF_DIR`/`ENC_RR_DIR` set to −1.)
3. **Motor directions** — `ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.12}}'`.
   All four wheel-tops must go **forward**. (Right side drove backward → `R_MOTOR_DIR`
   set to −1.) `velL`/`velR` positive on forward = motor dir & encoder dir agree
   (required, or the PID runs that side to full speed).
4. **Rotate** — `angular: {z: 0.5}` → wheels counter-rotate.
> Note: `/wheel_odom` won't echo on the host yet (see §0 open item) — the bench test
> reads velocities from the **serial** heartbeat instead.

### Phase 2 — floor calibration
- Drive a measured **1.0 m** straight; compare `/wheel_odom` `position.x`. Adjust
  `WHEEL_DIAMETER_M` / `ENCODER_CPR` if off.
- Spin **360°** in place; adjust `WHEEL_BASE_M` until odom yaw matches.
- If speed oscillates or lags, tune PID `Kp` / `Ki`.

### Phase 3 — fuse into the Jetson EKF
Once `/wheel_odom` is trustworthy, add to `orin-nav-stack/config/ekf.yaml`:
```yaml
    # wheel odometry (ESP32 encoders) — fuse body vx
    odom1: /wheel_odom
    odom1_config: [false, false, false,
                   false, false, false,
                   true,  false, false,
                   false, false, false,
                   false, false, false]
    odom1_differential: false
    odom1_relative: false
    odom1_queue_size: 10
```
(vx only; yaw-rate stays with the D555 gyro, which is better through pivots where
wheels scrub.) Then bring up fusion: `./run_stack.sh fuse`.

---

## 9. ROS interface summary

| Topic | Type | Dir (ESP32) | QoS | Purpose |
|---|---|---|---|---|
| `/cmd_vel` | geometry_msgs/Twist | subscribe | best_effort | target body vx, wz |
| `/wheel_odom` | nav_msgs/Odometry | publish | best_effort | encoder odom (frame odom→base_link), twist vx/vyaw for EKF — see §0 size caveat |

Watchdog: no `/cmd_vel` for 500 ms → motors stop. Agent-reconnect state machine drops
only after 3 consecutive missed pings (reduces WiFi flapping) and recovers from agent
restart / Pi5 reboot / WiFi drop without power-cycling. This node publishes **no TF** —
the Jetson robot_localization EKF owns `odom→base_link` (`config/ekf.yaml`).

---

## 10. History

- **v1** (`rover_firmware.ino`, Pi5 `~/ros2_ws/ESP_32_frimware/`): L298N, open-loop
  bang-bang, 51% PWM floor, no encoders, camera pan/tilt servos on GPIO18/19.
  **Invalid for this hardware** — kept only for reference/rollback.
- **v2** (this): BTS7960 + 4 encoders + per-side PID + `/wheel_odom`. Servos dropped
  (18/19 now motor PWM). Encoders on 3V3.
