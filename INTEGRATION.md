# ESP32 + Pi5 Integration Guide

Step-by-step to get the rover moving over WiFi using micro-ROS.

---

## Hardware Required

| Item | Notes |
|------|-------|
| ESP32 Dev Module | Any 38-pin variant |
| L298N motor driver | Dual H-bridge |
| 4-wheel chassis + DC motors | |
| PoE switch or WiFi router | All devices on same subnet |
| Logitech USB camera | Plugs into the **Jetson** (current). Publishes `/camera/color/image_raw` |
| RealSense D555 (PoE) | *Future depth-camera upgrade* — connects to Jetson via PoE (enables SLAM/Nav2/nvblox) |

> **No IR sensor** — obstacle detection will be handled by the D555 depth camera + nvblox on
> Jetson once it arrives. Until then the Logitech cam feeds vision (`local_agent` + Moondream).

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
| `micro_ros_arduino` | Jazzy release |
| `ESP32Servo` | latest (optional, for head servo) |

> `micro_ros_arduino` must match your ROS2 distro.
> Download the **Jazzy** `.zip` from:
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
// No IR_PIN — obstacle detection handled by D555 + nvblox on Jetson
```

Flash to ESP32. Open Serial Monitor at 115200 baud — you should see:
```
=== Rover ESP32 starting ===
[INIT] Motors, PWM ready
[WiFi] Connecting to agent at 192.168.1.100:8888
```
The ESP32 will keep retrying until the Pi5 micro-ROS agent is running.

---

## Pi5 Setup

### 1. ROS2 Jazzy

Install the base variant — no desktop or GUI tools needed on Pi5, saves ~400 MB RAM:

```bash
sudo apt update
sudo apt install ros-jazzy-ros-base
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

### 2. micro-ROS agent

Build in a **dedicated workspace** (`~/microros_ws`), separate from the application workspace.
This avoids polluting `pi5_ros2_ws` with a build-tool package and keeps `colcon build` fast.

```bash
# One-time build (takes ~10 min on Pi5)
mkdir -p ~/microros_ws/src
cd ~/microros_ws
git clone https://github.com/micro-ROS/micro_ros_setup src/micro_ros_setup
source /opt/ros/jazzy/setup.bash
rosdep update
rosdep install --from-paths src --ignore-src -r -y
colcon build
source install/setup.bash

# Create the micro-ROS agent
ros2 run micro_ros_setup create_agent_ws.sh
ros2 run micro_ros_setup build_agent.sh
source install/setup.bash

# Add to ~/.bashrc so every new shell sources it
echo "source ~/microros_ws/install/setup.bash" >> ~/.bashrc
```

After this, `micro_ros_agent` is available system-wide; the `robot_brain` launch file calls it by name.

### 3. Python and ROS2 dependencies

```bash
# ROS2 package dependencies (nav2_msgs, action_msgs, std_srvs, etc.)
cd ~/pi5_ros2_ws
sudo rosdep init   # skip if already done
rosdep update
rosdep install --from-paths src --ignore-src -r -y

# Python dependencies — pinned versions for reproducible installs
pip install -r requirements.txt
```

To update a pinned version: edit `requirements.txt`, then re-run `pip install -r requirements.txt`.
To add a new Python library: add it to both `requirements.txt` (with pinned version) and `src/ai_agent/setup.py` `install_requires`.

### 4. Set a static IP on Pi5

Edit `/etc/dhcpcd.conf` (or use your router's DHCP reservation):
```
interface wlan0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
```
This must match `AGENT_IP` in the firmware.

> On Ubuntu 24.04 without dhcpcd, use NetworkManager:
> ```bash
> nmcli con mod "WiFi-connection-name" ipv4.addresses 192.168.1.100/24
> nmcli con mod "WiFi-connection-name" ipv4.method manual
> nmcli con up "WiFi-connection-name"
> ```

### 5. Build the workspace

```bash
cd ~/pi5_ros2_ws
colcon build --symlink-install
source install/setup.bash
```

---

## Launch

### Full system (micro-ROS agent + LangGraph brain)

```bash
ros2 launch robot_brain brain_launch.py
```

This starts:
1. `micro_ros_agent` — UDP transport on port 8888, bridges Pi5 ↔ ESP32
2. `agent_node` — LangGraph brain, subscribes to `/voice/user_input`, publishes to `/goal_pose` and `/cmd_vel`

### Custom options

```bash
# Different LLM endpoint
ros2 launch robot_brain brain_launch.py base_url:=http://singireddys-mac-mini.local:8080/v1

# Different micro-ROS agent port
ros2 launch robot_brain brain_launch.py agent_port:=9999
# (update AGENT_PORT in firmware to match)
```

### Start order

1. **Mac Mini:** `./llama-server -m model.gguf --port 8080 -ngl 99`
2. **Jetson:** `docker compose up` (isaac_ros + ai_stack containers)
3. **Pi5:** `ros2 launch robot_brain brain_launch.py`
4. **ESP32:** power on — auto-connects to Pi5 micro-ROS agent over WiFi

---

## Verify the Connection

After launching, confirm the ESP32 is connected:

```bash
# Should show /cmd_vel among others
ros2 topic list

# Manual test: drive forward ~20 cm
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.2}, angular: {z: 0.0}}"

# Stop
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.0}, angular: {z: 0.0}}"

# Check Pi5 ↔ Jetson topics are visible
ros2 topic list | grep -E "voice|vision|goal_pose|camera"
```

Serial Monitor on ESP32 should show:
```
[READY] Rover micro-ROS ready
```

---

## Network Topology

```
192.168.1.x subnet (all devices same router)

Mac Mini  (singireddys-mac-mini.local)   :8080  llama.cpp HTTP
Jetson    (static or DHCP)      :0     ROS2 DDS (ROS_DOMAIN_ID=0)
Pi5       192.168.1.100         :8888  micro-ROS UDP agent
Camera    Logitech USB → Jetson —      Jetson publishes /camera/color/image_raw (D555 PoE later)
ESP32     DHCP                  —      connects to Pi5:8888 via WiFi UDP
```

All ROS2 devices must be on the same subnet with `ROS_DOMAIN_ID=0`.

---

## Full Topic Reference

| Topic | Type | Direction | Description |
|-------|------|-----------|-------------|
| `/cmd_vel` | `geometry_msgs/Twist` | Pi5 → ESP32 | Wheel velocities: `linear.x` m/s, `angular.z` rad/s |
| `/goal_pose` | `geometry_msgs/PoseStamped` | Pi5 → Jetson Nav2 | Map-based navigation goal |
| `/voice/user_input` | `std_msgs/String` | Jetson → Pi5 | STT transcription (triggers LangGraph) |
| `/voice/robot_speech` | `std_msgs/String` | Pi5 → Jetson | TTS text to Kokoro |
| `/camera/color/image_raw` | `sensor_msgs/Image` | Jetson (Logitech) → Pi5 | Compressed frames (~640×480, ~5fps); cached on Pi5, sent to Gemma on `look()` |
| `/vision/query` | `std_msgs/String` | Pi5 → Jetson | Moondream VLM question (navigation) |
| `/vision/query_result` | `std_msgs/String` | Jetson → Pi5 | Moondream VLM answer |
| `/visual_slam/tracking/odometry` | `nav_msgs/Odometry` | Jetson → Pi5 | Robot pose from Isaac ROS SLAM |
| `/brain/thinking` | `std_msgs/Bool` | Pi5 internal | True while LLM running |

**Removed topics:**

| Topic | Reason removed |
|-------|----------------|
| `/movement_cmd` | `chassis_pilot` removed — Pi5 publishes Twist directly |
| `/ir_obstacle` | IR sensor removed — D555 + nvblox handles obstacle detection |
| `/vision/objects_3d` | `spatial_node` removed — nvblox covers 3D mapping |
| `/vision/image_raw` | Replaced by `/camera/color/image_raw` (Logitech on Jetson now; D555 PoE later) |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| ESP32 Serial shows `[EXECUTOR ERROR]` repeatedly | micro-ROS agent not running | Start Pi5 launch first, then power ESP32 |
| Motors don't move but Serial shows commands | ENA/ENB jumpers still on | Remove jumpers, wire ENA→GPIO14, ENB→GPIO27 |
| Robot goes in circles instead of straight | Left/right motor wires swapped | Swap IN1↔IN3 or reverse one motor's wires |
| `ros2 topic list` doesn't show `/cmd_vel` | ESP32 not connected to agent | Check WiFi SSID/password and `AGENT_IP` in firmware |
| Robot stops mid-move | CMD_TIMEOUT_MS watchdog firing | Normal — ESP32 stops if no Twist received in 500ms |
| `/voice/user_input` not visible on Pi5 | Jetson containers not running | Start both docker containers on Jetson first |
| Nav2 goal published but robot doesn't move | Isaac ROS not running or SLAM not initialised | Check Jetson isaac_ros container, run SLAM test first |
| LLM calls failing | Wrong endpoint | Verify `singireddys-mac-mini.local` resolves: `ping singireddys-mac-mini.local` |

---

## Nav2 / SLAM Notes

Isaac ROS SLAM (visual SLAM) runs in Container 1 on Jetson.
Nav2 runs inside the same container and publishes `/cmd_vel` Twist when navigating.
Those Twist messages travel over DDS to Pi5, then through micro-ROS agent to ESP32.

**To build a SLAM map:**
1. Launch Jetson containers (isaac_ros must be running)
2. Launch Pi5: `ros2 launch robot_brain brain_launch.py`
3. Drive the robot manually using direct `/cmd_vel` publishes or voice commands
4. Save map when done (Nav2 map server — see Jetson SETUP.md)
5. Record (x, y, yaw) of each named location and update `agent_params.yaml`
