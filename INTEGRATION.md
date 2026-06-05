# ESP32 + Pi5 Integration Guide

Step-by-step to get the rover moving over WiFi using micro-ROS2.

---

## Hardware Required

| Item | Notes |
|------|-------|
| ESP32 Dev Module | Any 38-pin variant |
| L298N motor driver | Dual H-bridge |
| 4-wheel chassis + DC motors | |
| IR obstacle sensor | Active LOW output |
| Servo (optional) | Head pan, GPIO 18 |
| PoE switch or WiFi router | All devices on same network |

---

## L298N Wiring

Remove the ENA and ENB jumpers — wire them to GPIO for PWM speed control.

```
L298N        ESP32 GPIO
─────────    ──────────
IN1     ──►  26
IN2     ──►  25
ENA     ──►  14   ← remove jumper, connect here
IN3     ──►  33
IN4     ──►  32
ENB     ──►  12   ← remove jumper, connect here
GND     ──►  GND

IR sensor OUT ──►  34  (sensor GND → ESP32 GND, VCC → 3.3V)
Servo signal  ──►  18  (optional)
```

> **Why ENA/ENB matter:** Without PWM on ENA/ENB the motors run full speed only.
> Nav2 sends variable-speed Twist commands — PWM lets the rover slow down for
> precise turning and smooth navigation.

---

## Arduino IDE Setup

### 1. Install ESP32 board support

In Arduino IDE → Preferences → Additional Board Manager URLs, add:
```
https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
```
Then: Tools → Board Manager → search `esp32` → install **esp32 by Espressif Systems**.

### 2. Install required libraries

In Arduino IDE → Library Manager, install:

| Library | Version |
|---------|---------|
| `micro_ros_arduino` | latest |
| `ESP32Servo` | latest |

> `micro_ros_arduino` must match your ROS2 distro (Humble/Iron/Jazzy).
> Download the correct `.zip` from:
> https://github.com/micro-ROS/micro_ros_arduino/releases
> Then: Sketch → Include Library → Add .ZIP Library

### 3. Flash settings

| Setting | Value |
|---------|-------|
| Board | ESP32 Dev Module |
| Upload Speed | 921600 |
| CPU Frequency | 240 MHz |
| Partition Scheme | Default 4MB |

---

## Configure the Firmware

Open `esp32_firmware/rover_firmware/rover_firmware.ino` and edit the top section:

```cpp
const char* WIFI_SSID  = "YOUR_SSID";       // your WiFi network name
const char* WIFI_PASS  = "YOUR_PASSWORD";   // your WiFi password
const char* AGENT_IP   = "192.168.1.100";   // Pi5 static IP (see below)
const uint16_t AGENT_PORT = 8888;           // must match Pi5 launch arg
```

If your wiring differs from the defaults, also edit the pin defines:
```cpp
#define IN1  26
#define IN2  25
#define ENA  14
#define IN3  33
#define IN4  32
#define ENB  12
#define IR_PIN    34
#define SERVO_PIN 18
```

Flash to ESP32. Open Serial Monitor at 115200 baud — you should see:
```
=== Rover ESP32 starting ===
[INIT] Motors, PWM, servo ready
[WiFi] Connecting to agent at 192.168.1.100:8888
```
The ESP32 will keep retrying WiFi connection until the Pi5 agent is running.

---

## Pi5 Setup

### 1. Set a static IP on Pi5

Edit `/etc/dhcpcd.conf` (or use your router's DHCP reservation):
```
interface wlan0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
```
This must match `AGENT_IP` in the firmware.

### 2. Install micro-ROS2 agent

```bash
sudo apt install ros-$ROS_DISTRO-micro-ros-agent
```
Or build from source if not available for your distro:
```bash
cd ~/pi5_ros2_ws
git clone https://github.com/micro-ROS/micro_ros_agent src/micro_ros_agent
colcon build --packages-select micro_ros_agent
```

### 3. Build the workspace

```bash
cd ~/pi5_ros2_ws
colcon build --symlink-install
source install/setup.bash
```

---

## Launch

### Full system (brain + chassis + micro-ROS2 agent)

```bash
ros2 launch robot_brain brain_launch.py
```

### Custom options

```bash
# Different LLM server
ros2 launch robot_brain brain_launch.py base_url:=http://192.168.1.50:8080/v1

# Different micro-ROS2 agent port
ros2 launch robot_brain brain_launch.py agent_port:=9999
# (update AGENT_PORT in firmware to match)
```

### Start order

1. **Mac Mini:** `./llama-server -m model.gguf --port 8080 -ngl 99`
2. **Jetson:** `ros2 launch robot_bringup_pkg robot.launch.py mode:=visual_assistant`
3. **Pi5:** `ros2 launch robot_brain brain_launch.py`
4. **ESP32:** power on — it auto-connects to Pi5 agent over WiFi

---

## Verify the Connection

After launching, confirm the ESP32 is connected:

```bash
# Should show /cmd_vel, /ir_obstacle, /servo_angle
ros2 topic list

# Watch IR sensor
ros2 topic echo /ir_obstacle

# Manual test: drive forward for ~1 second
ros2 topic pub --once /movement_cmd std_msgs/msg/String "data: 'F:12'"

# Stop
ros2 topic pub --once /movement_cmd std_msgs/msg/String "data: 'S'"
```

Serial Monitor on ESP32 should show:
```
[READY] Rover micro-ROS2 ready
```

---

## Full Topic Reference

| Topic | Type | Direction | Description |
|-------|------|-----------|-------------|
| `/cmd_vel` | `geometry_msgs/Twist` | Pi5 → ESP32 | Wheel velocities. `linear.x` m/s, `angular.z` rad/s |
| `/ir_obstacle` | `std_msgs/Bool` | ESP32 → Pi5 | `true` = obstacle within IR range |
| `/servo_angle` | `std_msgs/UInt16` | Pi5 → ESP32 | Head servo angle 0–180° |
| `/movement_cmd` | `std_msgs/String` | LangGraph → chassis_pilot | `F:30`, `B:20`, `L:90`, `R:45`, `S` |
| `/voice/user_input` | `std_msgs/String` | Jetson → Pi5 | STT transcription (triggers LangGraph) |
| `/voice/robot_speech` | `std_msgs/String` | Pi5 → Jetson | TTS text |
| `/vision/query` | `std_msgs/String` | Pi5 → Jetson | VLM question |
| `/vision/query_result` | `std_msgs/String` | Jetson → Pi5 | VLM answer |
| `/vision/objects_3d` | `std_msgs/String` | Jetson → Pi5 | JSON: YOLO detections + distance |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| ESP32 Serial shows `[EXECUTOR ERROR]` repeatedly | micro-ROS2 agent not running | Start Pi5 launch first, then power ESP32 |
| Motors don't move but Serial shows commands | ENA/ENB jumpers still on | Remove jumpers, wire ENA→GPIO14, ENB→GPIO12 |
| Robot goes in circles instead of straight | Left/right motor wires swapped | Swap IN1↔IN3 or reverse one motor's wires |
| IR sensor always true | Wrong pin or sensor logic inverted | Check GPIO 34 with `ros2 topic echo /ir_obstacle`, adjust `IR_PIN` |
| `ros2 topic list` doesn't show `/cmd_vel` | ESP32 not connected to agent | Check WiFi SSID/password and `AGENT_IP` in firmware |
| Robot stops mid-move | CMD_TIMEOUT_MS watchdog firing | Normal — ESP32 stops if no Twist received in 500ms |

---

## Nav2 / SLAM (Future)

When you add Nav2 on Jetson, it will publish `/cmd_vel` Twist directly.
`chassis_pilot` already subscribes to `/cmd_vel` and forwards it to the ESP32 —
**no firmware or chassis_pilot changes needed.**

The only addition will be:
- RTAB-Map on Jetson consuming D555 depth + IMU → publishing `/odom`
- Nav2 consuming `/odom` → publishing `/cmd_vel`
- Two new LangGraph tools: `save_waypoint(name)` and `navigate_to_waypoint(name)`
