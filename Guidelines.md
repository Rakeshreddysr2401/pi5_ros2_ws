# Pi5 Robot Brain — Guidelines & Troubleshooting Guide

This guide compiles essential configuration rules, syntax fixes, and debugging workflows discovered during setup and testing of the LangGraph reasoning brain on Raspberry Pi 5.

---

## 1. LLM Endpoint Configurations

### llama.cpp Server (Mac Mini)
- **Hostname**: `singireddys-mac-mini.local` (resolves to Mac Mini IP).
- **Default Port**: `8080` (API path `/v1`).
- **Endpoint**: `http://singireddys-mac-mini.local:8080/v1`
- Make sure the server is launched on the Mac Mini before testing:
  ```bash
  ./llama-server -m <model-name>.gguf --port 8080 -ngl 99
  ```

### Cloud OpenAI Backend (Fallback)
If the local Mac Mini server is offline or unreachable, you can switch to OpenAI's official cloud backend:
- Set **provider** to `openai`.
- Set **model** to `gpt-4o-mini` (or your preferred GPT model).
- Provide the API key via the `OPENAI_API_KEY` environment variable.
- **CRITICAL**: Launch with `base_url:=https://api.openai.com/v1` to override the launch parameter default, preventing the client from redirecting OpenAI requests to the local Mac Mini host.

---

## 2. ROS 2 Parameters YAML Syntax

All parameter files in ROS 2 must nest variables under the node namespace block followed by a `ros__parameters:` key. Missing `ros__parameters:` will cause node parsing failures on startup.

**Incorrect format:**
```yaml
agent_node:
  provider: "llamacpp"
```

**Correct format (implemented in `agent_params.yaml`):**
```yaml
agent_node:
  ros__parameters:
    provider: "openai"
    model: "gpt-4o-mini"
    api_key_env: "OPENAI_API_KEY"
    base_url: "https://api.openai.com/v1"
```

---

## 3. LangGraph Tool Mapping & Recursion Loop Prevention

### Tool Binding Requirement
Any tool mentioned in an agent's system prompt or guidelines (e.g. `handover`, `speak`) **must** be present in that agent's tool set array in [tools/__init__.py](file:///home/rakhi24/ros2_ws/src/ai_agent/ai_agent/graph/tools/__init__.py) (e.g. `CHAT_TOOLS`, `NAVIGATE_TOOLS`). If a tool is listed in the system prompt instructions but not bound to the model, it causes validation or model-routing failures.

### Loop Prevention via Handover Control
To prevent infinite routing loops between specialized agents and the supervisor (e.g. `navigate` $\rightarrow$ `supervisor` $\rightarrow$ `navigate` $\rightarrow$ `supervisor`...):

1. **Text Response Backpressure**: The specialized agent must output a final natural language confirmation message to the user (e.g., *"I've completed the movement."*) in its final response.
2. **Non-Chaining Handover**: The agent must call `handover("supervisor")` with `chain=False` in the same step.
3. **Execution Termination**: When the node outputs text (`ai_content` is not empty) and uses `chain=False`, the LangGraph `handle_handover` router returns a sticky status update instead of executing a `Command(goto="supervisor")`. This terminates the current graph execution turn and lets the user speak next. On the next turn, the conversation starts directly at the supervisor.

---

## 4. Run & Test Commands

### 1. Build Workspace
Make sure to compile parameters and launch script modifications:
```bash
cd ~/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

### 2. Launching with local llama.cpp VLM
```bash
source /opt/ros/jazzy/setup.bash
source ~/microros_ws/install/setup.bash
source install/setup.bash
ros2 launch robot_brain brain_launch.py
```

### 3. Launching with OpenAI Cloud LLM
```bash
export OPENAI_API_KEY="your-sk-key"
source /opt/ros/jazzy/setup.bash
source ~/microros_ws/install/setup.bash
source install/setup.bash
ros2 launch robot_brain brain_launch.py provider:=openai base_url:=https://api.openai.com/v1
```

### 4. Simulating/Testing User Voice Inputs
Publish a test string to the reasoning brain subscription to verify behavior:
```bash
source /opt/ros/jazzy/setup.bash
ros2 topic pub --once /voice/user_input std_msgs/msg/String "{data: 'move forward 10 cm'}"
```
