# LangRobo Pi5 brain — project guide

Home robot "Rakhi": Pi5 (this repo) runs the LangGraph brain; Jetson Orin runs
perception in rover mode (cuVSLAM/nvblox/Nav2/YOLO, `orin-nav-stack`); voice (STT/TTS, separate `speech_vision` repo) is a DIFFERENT role, OFF on the Orin in rover mode; a second, independent CPU-only voice pair (`pi5_voice_pkg`, this repo) now runs
locally on the Pi5 instead, so voice works concurrently with driving — see PI5_VOICE.md; Mac Mini serves the LLM
(llama.cpp, `singireddys-mac-mini.local:8080`); ESP32 drives the wheels.
Read HOW_IT_WORKS.md for the end-to-end walkthrough (boot, turn lifecycle,
failure paths); ARCHITECTURE.md before touching graph/agent code;
OPERATIONS.md for run/deploy/troubleshooting; PRODUCT.md for the roadmap; PI5_VOICE.md for the local STT/TTS pair; the Jetson `orin-nav-stack/SYSTEM_INTEGRATION.md` for the cross-machine ROS contract.

## Fleet start — one command brings up the whole robot

`scripts/fleet.sh {sim|rover|stop|down|status}` (run here on the Pi5). Picks the robot **body**:

- **`sim`** — the SIMULATION body: sshes the laptop and starts its Gazebo sim + Nav2
  (`rover_sim`), and starts BOTH Jetson roles — `ai_stack` voice (you still talk to the
  robot by real mic/speaker while the body is simulated) and the `isaac_ros` perception
  container (nvblox/SLAM consuming the sim's /cam_1 depth/RGB). Also switches the brain's
  `robot_body` to `sim` (cmd_vel becomes TwistStamped on /mecanum_drive_controller/cmd_vel).
- **`rover`** — the REAL body: starts this Pi5's micro-ROS agent (ESP32 wheels) and the
  Jetson's `isaac_ros` perception role in REAL mode (D555 + cuVSLAM localization +
  nvblox + Nav2 + YOLO detections_3d — see JETSON_D555_SETUP.md). Voice is OFF on the
  Jetson in this mode (perception owns the 8GB Orin; cuVSLAM RUNS on Orin (standalone pyCuVSLAM cu12 wheel) —
  cuVSLAM is the localizer): talk to the robot via Telegram, start voice manually
  with `fleet_role.sh voice start` (won't fit alongside perception — see PI5_VOICE.md),
  or `ros2 launch pi5_voice_pkg voice_launch.py` here for CPU-only voice that runs
  fine alongside it. Switches `robot_body` back to `rover` (plain Twist
  on /cmd_vel).
- **`stop`** parks the robot: stops the body (sim + Jetson roles) but keeps `langrobo-brain`
  + `langrobo-discovery` up, so chat/Telegram keeps listening. No password.
- **`down`** full shutdown: everything `stop` does PLUS this Pi5's system units (brain,
  micro-ROS, discovery) via sudo. Those units are `enabled`, so a Pi5 reboot restarts them.
- **`status`** shows who's up everywhere.

`langrobo-discovery` (the DDS meeting point) and `langrobo-brain` run here in **both** modes;
fleet.sh ensures them. Each machine can still be driven on its own — the laptop via
`rover_sim/.../fleet_sim.sh`, the Jetson via `~/robot/scripts/fleet_role.sh {voice|perception}`.
Reaches the other machines by mDNS name over passwordless ssh (see NETWORKING.md); the
laptop's key + sshd were set up 2026-07-07 so the Pi5→laptop hop works.

## Commands

```bash
# Whole robot (see "Fleet start" above)
./scripts/fleet.sh sim        # simulation body (laptop sim + jetson voice+isaac_ros)
./scripts/fleet.sh rover      # real body (pi5 microros + jetson perception; voice=Telegram)
./scripts/fleet.sh stop       # park robot body (brain stays up)
./scripts/fleet.sh down       # full shutdown incl. Pi5 services (sudo)
./scripts/fleet.sh status

# Test (pure core — no robot, no LLM, no keys; ~4s)
cd src/langrobo_core && python3 -m pytest tests/ -q

# Build + deploy after code changes
colcon build --symlink-install && sudo systemctl restart langrobo-brain

# Run modes (NEVER two at once — both drive /cmd_vel + UDP 8888)
systemctl status langrobo-brain langrobo-microros   # production (systemd)
ros2 launch langrobo_ros brain_launch.py            # foreground all-in-one
./scripts/dev.sh                                    # LangGraph Studio :2024
./scripts/dev_voice.sh                              # Studio :2024 + STT/TTS (talk to it in dev mode)

# Observe
journalctl -u langrobo-brain -f -o cat              # JSON logs (jq-able, trace_id per turn)
curl -s localhost:8090/status | jq                  # LLM/memory/turn state
python3 scripts/latency_replay.py "utterance"       # per-stage latency waterfall

# Python deps (system python, PEP 668)
pip3 install --break-system-packages -r requirements.txt
```

## Hard rules

1. **`src/langrobo_core` must never import rclpy** (pure zone — pip package).
   Only `src/langrobo_ros` (agent_node.py, ros2_bridge.py) touches ROS2. Tools
   that need ROS message types import them lazily *inside* the function body.
2. **KV-cache discipline** (violations cost ~20s/turn on the 12B model):
   - Never put a clock/timestamp in a system prompt (date only; clock is the
     `get_current_time` tool).
   - Message projection must stay append-only (`utils/message_utils.py`
     docstring); never drop/reorder mid-history messages.
   - History trims only at HumanMessage boundaries (`utils/history.py`).
   - Don't auto-inject retrieved memory into system prompts — recall is
     tool-driven (`recall_memory`) on purpose.
3. **No `speak()` tool** — an agent's reply text IS the speech (streamed
   sentence-by-sentence, utterance closed with `<|eou|>`). The wire protocol
   with the Jetson (`/voice/*`, `<|eou|>`) must match tts_node — change both
   repos together or neither.
4. **Missing keys degrade, never crash**: no Swiggy token → agent says
   unavailable; no Tavily key → no web search; Mac Mini down → cloud fallback
   or spoken offline message. Keep this property when adding features.
5. New proactive behaviour = a producer injecting `[SYSTEM]` turns into
   agent_node's system queue (see reminder poll) — don't invent new mechanisms.

## Layout (detail in ARCHITECTURE.md)

- `langrobo_core/graph/` — topology (build.py), routing registry, handover
  resolution + loop guards
- `langrobo_core/fastpath.py` — deterministic movement lane: exact spoken
  movement commands ("stop", "come here", "go near the chair", "forward 30")
  execute tools directly with ZERO LLM calls (agent_node hook, `fast_path`
  param, default on); anything ambiguous falls through to the graph
- `langrobo_core/prompts.py` — EVERY system prompt (agents + background jobs);
  agents append only dynamic blocks (household, now-playing, date) in-module
- `langrobo_core/agents/` — one module per agent (node fn + context assembly)
- `langrobo_core/tools/` — @tool functions; per-agent sets in `__init__.py`;
  robot I/O via `_bridge.get()`
- `langrobo_core/services/` — config (validated .env), llm (slots + fallback),
  mcp (remote MCP provider registry: Swiggy food/instamart/dineout + token
  lifecycle — future MCPs are one ProviderSpec + the add-an-agent recipe),
  memory (embedded Qdrant + fastembed), consolidation (nightly episodic→facts,
  local model only), knowledge (document ingest for the knowledge agent),
  briefing (once-daily scheduler), watch (armed person-detection alerts →
  Telegram), telegram (channel: long-poll + sends),
  permissions (role→capability policy — enforced in tools, never only prompts),
  health (FastAPI :8090), studio (LangGraph Server client — dev mode only),
  logging, metrics
- `langrobo_ros/` — agent_node (params, queues, worker loop, cache warmer),
  studio_voice_node (dev-mode voice ↔ `langgraph dev`, see OPERATIONS.md),
  ros2_bridge (all topics/services/actions), launch, systemd units

Adding an agent/tool: recipes at the bottom of ARCHITECTURE.md (registry +
build.py + handover Literal must stay in sync — the smoke tests catch drift).

## Config split

- `src/langrobo_ros/config/agent_params.yaml` — LLM provider/model/slots,
  locations (ROS params)
- `.env` (validated fail-fast at startup) — keys + LANGROBO_* service settings;
  full table in OPERATIONS.md; template in example.env
- Robot state lives in `~/.langrobo/` (household.json, reminders.json,
  errands.json, qdrant/, telegram_offset, telegram_deferred.json, watch.json,
  consolidation.json)

## Working on the Jetson from here

Passwordless SSH: `ssh rakhi24@rakhi-jetson.local`. The direct ethernet link is
BACK as of 2026-07-10 (Pi5 192.168.2.10 ↔ Jetson 192.168.2.20) alongside wifi;
everything stays NAME-based so either link works — mDNS resolves over whatever
is up, the discovery server binds 0.0.0.0, and the Jetson containers point at
`rakhi24-desktop.local:11811`. Unplugging a link mid-session needs only a role
restart (names re-resolve at launch); see NETWORKING.md. The speech_vision repo
is at `~/robot` on the Jetson (branch dev-1.0.7) and has its own
CLAUDE.md + VOICE_PIPELINE.md — read those before editing; they document the
container build/restart procedure and five hard-won gotchas (venv-python
colcon builds, zombie launch children, pinned pip index, broken torchaudio,
ec_speaker audio routing). Workflow: edit via ssh/rsync on the host paths
(`~/robot/ai_ws` is bind-mounted into the `ai_stack` container), build and
restart via `docker exec`, test over ROS2 topics from this machine, commit in
`~/robot` over ssh. The `/voice/*` + `/audio/*` topic contract is shared —
change both repos together or neither.

## Gotchas

- Real-robot Nav2/SLAM software is DEPLOYED (Jetson `langrobo_perception`
  mode:=real — cuVSLAM + nvblox + Nav2, smoke-tested camera-less) and waits
  only for the D555 hardware; JETSON_D555_SETUP.md is the camera-day
  checklist + acceptance tests. Without the camera, `navigate_to_pose`/
  `approach_object` still report honestly after a 10s wait. New depth contract: Jetson publishes map-frame objects on
  `/vision/detections_3d` (JSON); brain drives the camera head via
  `/servo_pan`+`/servo_tilt` (ESP32, GPIO 18/19) and mirrors angles on
  `/camera/pan_tilt_state` for the Jetson TF broadcaster.
- `strict_tool_calls` + streaming need the llama.cpp server started with
  `--jinja --parallel 5` (chat/local_agent/specialist/supervisor/navigate slots).
- Smoke tests import `services/mcp.py` (via `tools/__init__`) which probes the
  network only when a Swiggy token exists (SWIGGY_ACCESS_TOKEN env or
  `~/.langrobo/mcp_tokens.json` — login via `scripts/swiggy_login.py`).
- Pi5↔Jetson clocks drift ~1.5s (chrony peering pending) — latency_replay
  flags negative deltas.

## Simulation laptop (rover_sim) — the stand-in robot body

Until the real rover exists, a Gazebo sim on the laptop (`rakhi24`, wifi DHCP)
plays the robot body: mecanum X3 rover with lidar + RealSense-D555-style RGBD
camera in a furnished house world, with Nav2 + slam_toolbox on top.
Repo: https://github.com/Rakeshreddysr2401/rover_sim (laptop path
`/workspace/ros2_ws/src/rover_sim`). Its `docs/INTERFACE.md` is the
topic/action/frame contract this brain should code against — same contract the
real rover must satisfy later.

- The sim joins our discovery server: on the laptop,
  `export ROS_DISCOVERY_SERVER=rakhi24-desktop.local:11811` before launching.
  Then this brain's `navigate_to_pose` tool talks to a real Nav2 server.
- Highlights of the contract: `/navigate_to_pose` (NavigateToPose),
  `/scan` 10 Hz, `/cam_1/color/image_raw` + `/cam_1/depth/image_rect_raw`
  15 Hz (D555 names — nvblox-ready), odom `/mecanum_drive_controller/odom`,
  cmd_vel is **TwistStamped** on `/mecanum_drive_controller/cmd_vel`
  (plain `/cmd_vel` exists only while its Nav2 is up).
- Sim runs ≈0.1× real time in the furnished house world (laptop iGPU) — don't
  tune wall-clock timeouts against it.
- Keep the machine/interface details in sync across the three CLAUDE.md files
  (this repo, `~/robot` on the Jetson, rover_sim) — change all or none.
