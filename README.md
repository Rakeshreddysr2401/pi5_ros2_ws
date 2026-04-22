# Pi 5 Cognition & Control Workspace

This workspace contains the **Brain and Muscles** of the distributed robot.

## Packages
- **ai_agent**: LangGraph supervisor and sub-agents for reasoning.
- **robot_brain**: Chassis pilot for motor control and navigation.
- **robot_interfaces**: Custom ROS 2 interfaces (Shared with Jetson).

## Setup
Refer to the main project documentation:
- [System Architecture](https://github.com/your-repo/docs/ARCHITECTURE.md)
- [Pi 5 Setup Guide](https://github.com/your-repo/docs/PI5_SETUP.md)

## Launch
```bash
ros2 launch robot_brain brain_launch.py
```
