# Pi5 Robot Brain — Device Setup Guide

This guide details how to set up the robot brain reasoning node and micro-ROS agent on a new device (Raspberry Pi 5 or Ubuntu-based controller) running **Ubuntu 24.04** and **ROS 2 Jazzy**.

---

## Prerequisites

Ensure ROS 2 Jazzy is installed. For headless robot setups, the base variant is recommended to save memory:
```bash
sudo apt update
sudo apt install -y ros-jazzy-ros-base
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

---

## Step-by-Step Setup

### Step 1: Install System & Build Dependencies
Install the package manager tools, VCS repository manager, and compilation libraries required for micro-ROS agent building:
```bash
sudo apt update
sudo apt install -y \
  python3-rosdep \
  python3-vcstool \
  flex \
  bison \
  libncurses-dev \
  libcurl4-openssl-dev
```

Initialize and update `rosdep` (if not already done):
```bash
sudo rosdep init
rosdep update
```

### Step 2: Install Python Packages
Since Ubuntu 24.04 uses an externally managed environment (PEP 668), install the pinned packages globally using the `--break-system-packages` flag:
```bash
cd ~/ros2_ws
pip3 install -r requirements.txt --break-system-packages
```

### Step 3: Install Main Workspace Dependencies
Use `rosdep` to download all other standard ROS 2 package dependencies declared in the packages:
```bash
cd ~/ros2_ws
rosdep install --from-paths src --ignore-src -r -y
```

### Step 4: Build the micro-ROS Agent Workspace
The micro-ROS agent is compiled in a dedicated workspace to keep main application builds fast.
> [!IMPORTANT]
> You **must** checkout the `jazzy` branch of `micro_ros_setup` to avoid version mismatches and package name errors (like `fastddsConfig.cmake` missing).

```bash
# 1. Create workspace and clone setup tool
mkdir -p ~/microros_ws/src
cd ~/microros_ws
git clone https://github.com/micro-ROS/micro_ros_setup.git src/micro_ros_setup

# 2. Checkout Jazzy branch and compile the setup tool
cd ~/microros_ws/src/micro_ros_setup
git checkout jazzy
cd ~/microros_ws
source /opt/ros/jazzy/setup.bash
colcon build

# 3. Create the agent sources and compile them
source install/setup.bash
ros2 run micro_ros_setup create_agent_ws.sh
ros2 run micro_ros_setup build_agent.sh
```

### Step 5: Configure Shell Sourcing (`~/.bashrc`)
Configure your shell to source the micro-ROS agent workspace and the main application workspace at startup. 

Add the following to the end of your `~/.bashrc` file:
```bash
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=30                   # Replace with your domain ID if different
source ~/microros_ws/install/setup.bash   # Sources the micro-ROS agent command
source ~/ros2_ws/install/setup.bash       # Sources the robot brain nodes
```
Source it in your current shell:
```bash
source ~/.bashrc
```

### Step 6: Build the Application Workspace
Finally, build the packages inside `ros2_ws`:
```bash
cd ~/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

---

## Verifying Setup

### 1. Check ROS 2 packages are recognized:
```bash
ros2 pkg list | grep -E "ai_agent|robot_brain"
```

### 2. Verify the micro-ROS agent is executable:
```bash
ros2 run micro_ros_agent micro_ros_agent --help
```
