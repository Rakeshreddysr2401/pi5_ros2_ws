# Pi5 Robot Brain — Guidelines & Troubleshooting Guide

This guide compiles essential configuration rules, syntax fixes, and debugging workflows discovered during setup and testing of the LangGraph reasoning brain on Raspberry Pi 5.

---

## 1. LLM Endpoint Configurations

### llama.cpp Server (Mac Mini)
- **Hostname**: `singireddys-mac-mini.local` (resolves to Mac Mini IP).
- **Default Port**: `8080` (API path `/v1`).
- **Endpoint**: `http://singireddys-mac-mini.local:8080/v1`
- **Model**: Gemma 3n E4B (Q8_0), **multimodal** — the `mmproj` vision projector must be
  loaded so `local_agent`'s `look()` images work. Confirm `/v1/models` reports
  `"capabilities": [..., "multimodal"]`.
- Make sure the server is launched on the Mac Mini before testing:
  ```bash
  ./llama-server -m gemma-4-E4B-it-Q8_0.gguf --mmproj mmproj-gemma-4-E4B-it-Q8_0.gguf \
                 --port 8080 -ngl 99 --parallel 4
  ```
- **Slots**: `--parallel N` gives N KV slots. `local_agent` can pin one via
  `local_agent_slot` so its cached image prefix isn't evicted by other agents. The
  server's prompt cache + unified KV already do automatic prefix reuse, so pinning is
  insurance (see [ARCHITECTURE.md](ARCHITECTURE.md) § Conversational Vision).

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

### Response Contract (speak vs. text)
Every agent follows the same rule so responses are reliable across models:

- **Put the actual answer in the message text.** `agent_node` auto-publishes the final
  AI text to `/voice/robot_speech` (TTS), and Studio shows it. An empty final message
  shows as "No data".
- **`speak()` is acknowledgement-only** — use it to say something *before* a slow tool
  runs (e.g. "let me look") so the user isn't left in silence. Do NOT wrap the final
  answer in `speak()`, or the final message ends up empty.

### Loop Prevention via Handover Control
Routing loops (e.g. `chat → chat → chat …`, or `navigate → supervisor → navigate …`) are
prevented structurally in `handle_handover`, independent of model behaviour:

1. **No self-handover**: if an agent hands over to itself, it is re-entered once with a
   hard "answer directly, do not hand over" nudge. Prompts also forbid self-handover.
2. **Deterministic loop guard**: a per-turn visit counter (`_MAX_VISITS_PER_AGENT = 3`,
   reset each user turn by `turn_entry`) applies to **every** agent — `chat` included,
   no exemptions. On exceeding it, `handle_handover` ends the turn with a plain fallback
   message **without calling the LLM again**, so a loop can never run away.
3. **Sticky vs. chain**: when an agent outputs text and calls `handover(..., chain=False)`,
   the router returns a sticky update (END) instead of `Command(goto=...)`. The turn ends;
   the next turn restarts at the supervisor. `chain=True` routes immediately in the same
   turn (e.g. `swiggy → tracker` after placing an order).

> Small models (Gemma 3n E4B) over-route — they may call `handover` when they should just
> answer. The guard above makes this safe; routing quality improves on Gemma 12B.

---

## 4. Conversational Vision (local_agent + look())

`local_agent` is the multimodal vision agent. It sees the **real camera frame** (via the
Gemma `mmproj`) and keeps the frame in the conversation for follow-ups. (There is no
on-device VLM — local Moondream doesn't fit the 8GB Jetson; rich vision is Gemma via `look()`.)

- **Capture is tool-driven**: the model calls `look()`, which reads the cached frame and
  injects it as a **HumanMessage image block** (OpenAI-compatible servers won't accept
  images in tool-role messages, so `look()` returns a text ack + an image message).
- **Follow-ups reuse the frame**: "did he wear spectacles?" reasons over the image already
  in history — no second `look()`. A fresh `look()` only happens for a new/changed view.
- **Per-agent projection**: the shared log keeps images, but only `local_agent` is fed them
  (`keep_images=True`); every other agent gets the image stripped to `[Current camera view]`.
- **Camera source**: Logitech USB cam on the **Jetson** publishes compressed
  `/camera/color/image_raw` (~640×480, ~5 fps); `agent_node` caches the latest frame and
  `look()` serves it. The Jetson `target_node` (YOLOv8n) consumes the same feed for
  navigation directions (`/vision/target` → `/vision/target_result`).

**Testing vision off-robot (no camera):** set `STUDIO_TEST_IMAGE=/path/to.jpg` — the
`StubBridge` serves that file to `look()` so you can exercise the full flow in Studio or a
headless script.

---

## 5. Run & Test Commands

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
