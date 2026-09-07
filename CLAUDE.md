# LangRobo Pi5 brain — project guide

Home robot "Rakhi". Four machines:

- **Pi 5** (this repo) — the LangGraph brain, **three agents**, plus a CPU-only
  STT/TTS pair (`pi5_voice_pkg`) so voice runs concurrently with driving.
- **Jetson Orin** — perception: cuVSLAM + nvblox + Nav2, and the phase-4 VLM
  bridge (`image_bridge` publishes the colour frame as JPEG for `look()`;
  `pixel_to_goal` turns a VLM-picked pixel into an odom-frame Nav2 goal).
  Repo: `-langrobo_perception-`, brought up with `./rover`.
- **Mac Mini** — the LLM and VLM (llama.cpp, `singireddys-mac-mini.local:8080`).
  **Must run with `--jinja --parallel 3`** — one KV slot per agent.
- **ESP32** — 50 Hz closed-loop PID on four wheels, micro-ROS over WiFi.

Read HOW_IT_WORKS.md for the end-to-end walkthrough (boot, turn lifecycle,
failure paths); **ARCHITECTURE_LLD.md before touching graph/agent code**;
INTEGRATION_GAPS.md before building anything that touches the world;
OPERATIONS.md for run/deploy/troubleshooting; PI5_VOICE.md for the STT/TTS pair.

## Fleet start — one command brings up the whole robot

`scripts/fleet.sh {sim|rover|stop|down|status}` (run here on the Pi5). Picks the robot **body**:

- **`sim`** — the SIMULATION body: sshes the laptop and starts its Gazebo sim + Nav2
  (`rover_sim`), and starts BOTH Jetson roles — `ai_stack` voice (you still talk to the
  robot by real mic/speaker while the body is simulated) and the `isaac_ros` perception
  container (nvblox/SLAM consuming the sim's /cam_1 depth/RGB). Also switches the brain's
  `robot_body` to `sim` (cmd_vel becomes TwistStamped on /mecanum_drive_controller/cmd_vel).
- **`rover`** — the REAL body: starts this Pi5's micro-ROS agent (ESP32 wheels) and
  the Jetson's perception role (D555 + cuVSLAM + nvblox + Nav2 + the phase-4 VLM
  bridge). Perception owns the 8 GB Orin, so Jetson voice is OFF in this mode —
  run `ros2 launch pi5_voice_pkg voice_launch.py` here instead (CPU-only, fits
  alongside), or use Telegram. Switches `robot_body` back to `rover` (plain
  Twist on `/cmd_vel`).

  **`./scripts/fleet.sh rover` does not start the Jetson's VLM bridge.** Without
  `./rover vlm` over there, `look()` has no camera frame and
  `approach_described_object` has no depth grounding.
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

# Test (pure core — no robot, no LLM server, no keys; 179 tests, ~1.5s)
cd src/langrobo_core && python3 -m pytest tests/ -q

# Build + deploy after code changes
colcon build --symlink-install && sudo systemctl restart langrobo-brain

# Run modes (NEVER two at once — both drive /cmd_vel + UDP 8888)
systemctl status langrobo-brain langrobo-microros   # production (systemd)
ros2 launch langrobo_ros brain_launch.py            # foreground all-in-one
./scripts/dev.sh                                    # LangGraph Studio :2024 (draws the graph)

# Observe
journalctl -u langrobo-brain -f -o cat              # JSON logs (jq-able, trace_id per turn)
curl -s localhost:8090/status | jq                  # LLM health, telegram, turn state
python3 scripts/latency_replay.py "utterance"       # per-stage latency waterfall

# Python deps (system python, PEP 668)
pip3 install --break-system-packages -r requirements.txt
```

## Hard rules

1. **`src/langrobo_core` must never import rclpy** (pure zone — pip package).
   Only `src/langrobo_ros` (agent_node.py, ros2_bridge.py) touches ROS2. Tools
   that need ROS message types import them lazily *inside* the function body.
2. **One llama.cpp KV slot per agent.** Slots are declared in `registry.py`
   (`AgentSpec.slot`), NOT as ROS params. Start the server with
   `--parallel 3`: each agent's ~900-token prompt prefix then stays resident
   in its own cache. Two agents on one slot evict each other every turn
   (~18-50s of re-prefill). agent_node probes the server's real slot count at
   boot, wraps with modulo, and warns loudly if it had to.
3. **KV-cache discipline** (violations cost ~20s/turn on the 12B model):
   - Never put a clock/timestamp in a system prompt (date only; clock is the
     `get_current_time` tool).
   - Message projection must stay append-only (`utils/message_utils.py`
     docstring); never drop/reorder mid-history messages.
   - History trims only at HumanMessage boundaries (`utils/history.py`).
   - Don't auto-inject retrieved memory into system prompts — recall is
     tool-driven on purpose (there is no episodic-memory tool in this cut —
     see ARCHITECTURE_LLD.md §8 if you add one back).
4. **No `speak()` tool** — an agent's reply text IS the speech (streamed
   sentence-by-sentence, utterance closed with `<|eou|>`). The two halves of
   that protocol are `langrobo_core/utils/speech_stream.py` and
   `pi5_voice_pkg/tts_node.py` — different packages, same repo. Change both or
   neither.
5. **Missing keys degrade, never crash**: no Tavily key → no web search and
   chat says it can't look that up; no Telegram allowlist → the channel stays
   off; Mac Mini down → cloud fallback or a spoken offline message. Keep this
   property when adding features.
6. New proactive behaviour = a producer injecting `[SYSTEM]` turns into
   agent_node's system queue (the Nav2 arrival report is the worked
   example) — don't invent new mechanisms.

## Layout (full detail in ARCHITECTURE_LLD.md)

**Three agents, no router.** Each owns one modality: `chat` (text in/out —
the default responder, and the one carrying the routing table), `local_agent`
(images — the only agent with `look()`), `navigate` (motion — the only agent
that moves wheels).

- `langrobo_core/registry.py` — **ONE `AgentSpec` per agent, and nothing about
  an agent lives anywhere else**: routing copy, prompt, tool set, KV slot,
  sticky/keep_images. Adding an agent = a name in `agent_ids.py` + a spec here;
  the graph, handover grammar, the routing table chat renders, sticky set and slot
  map all derive from it. THREE import-time asserts catch drift: registry vs
  agent_ids, every routable target has a spec, and no duplicate KV slot.
- `langrobo_core/prompts.py` — EVERY system prompt. `SPEECH_STYLE` (inside
  PERSONA) is the ONE spoken-output contract — don't restate it per agent.
  Tool lists are NOT hand-written: prompts carry a `{tools}` placeholder that
  `render_tools()` fills from the bound tool set.
- `langrobo_core/fastpath.py` — **two shortcuts, both of which must never
  guess**:
  - `match()` — the movement lane. Exact spoken commands ("stop", "forward 30",
    "go to the kitchen") execute tools directly with ZERO LLM calls.
  - `is_vision_question()` — decides where the GRAPH starts. A certain vision
    question ("what do you see?") gets the camera frame attached by agent_node
    and enters `local_agent` directly: one LLM call instead of three.
  Anything either one is unsure about falls through to the normal graph. A test
  asserts the two lanes never both claim the same utterance — a movement
  command must never be answered with a photo.
- `langrobo_core/graph/` — topology (build.py, derived entirely from
  registry.py), entry routing (sticky agent, else chat), handover + loop guards
- `langrobo_core/agents/` — `factory.py` builds EVERY agent from its spec;
  there are no hand-written nodes
- `langrobo_core/tools/` — @tool functions; per-agent sets in `__init__.py`;
  robot I/O via `_bridge.get()`. Keep the sets SHORT: every tool is shipped as
  a schema on every turn to that agent, forever.
- `langrobo_core/services/` — state that outlives a turn: config (validated
  .env), llm (slots + cloud fallback), telegram (long-poll + sends),
  permissions (role→capability, enforced in tools not prompts), health
  (FastAPI :8090), logging, metrics
- `langrobo_core/bridges/stub.py` — the no-ROS bridge. **Must mirror
  ROS2Bridge's public surface**; a missing method surfaces as an
  AttributeError inside a tool, which the model reports as a robot fault.
- `langrobo_ros/` — agent_node (params, queues, worker loop, cache warmer),
  ros2_bridge (all topics/services/actions; `NAV_FRAME` defined once here),
  launch, systemd units

## Config split

- `src/langrobo_ros/config/agent_params.yaml` — LLM provider/model/slots,
  locations (ROS params)
- `.env` (validated fail-fast at startup) — keys + LANGROBO_* service settings;
  full table in OPERATIONS.md; template in example.env
- Robot state lives in `~/.langrobo/` (locations.json, telegram_offset,
  telegram_deferred.json)

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

- **Read [INTEGRATION_GAPS.md](INTEGRATION_GAPS.md) before building anything
  that touches the world.** It is the cross-repo view neither repo's own tests
  can produce: which topics have publishers, the drive calibration, the frame
  rule, and the voice gates.

- **The rover is real and driving.** The D555 is mounted and streaming;
  cuVSLAM + nvblox + Nav2 run on the Jetson and have driven autonomous goals
  to within 4-5 cm. Bring it up with `./rover nav && ./rover vlm` in the
  perception repo.
- **Everything is `odom`, never `map`.** That Nav2 runs single-session with no
  relocalisation, so no node publishes a map frame at all. Goals, TF pose
  reads and any object positions must agree on `ROS2Bridge.NAV_FRAME`
  (default `odom`, one constant, `LANGROBO_NAV_FRAME` to override). A `map`
  goal is not an error — it is silently untransformable, which is how every
  `navigate_to_pose` call failed for months.
- **Four topics this brain used to speak have no listener on the rover** and
  the tools built on them were removed rather than left looking functional:
  `/vision/detections_3d` (object positions — the contract to restore it is in
  INTEGRATION_GAPS.md §1), `/vision/target*` (YOLO servo loop), `/servo_pan` +
  `/servo_tilt` (no mount, and the ESP32 firmware has three subscriptions and
  none are servos), `/audio/music_*`. Check for a publisher before building on
  a topic here.
- Streaming tool calls need the llama.cpp server started with
  `--jinja --parallel 3` (one slot per agent: chat/local_agent/navigate).
- Pi5↔Jetson clocks drift ~1.5s (chrony peering pending) — latency_replay
  flags negative deltas.

## Simulation laptop (rover_sim) — the second body

**The real rover exists and drives** (see Gotchas). The sim is no longer a
stand-in — it is a second body you can develop against without a robot in the
room, selected with `./scripts/fleet.sh sim`.

A Gazebo sim on the laptop (`rakhi24`, wifi DHCP): mecanum X3 rover with lidar
+ RealSense-D555-style RGBD camera in a furnished house world, Nav2 +
slam_toolbox on top. Repo:
https://github.com/Rakeshreddysr2401/rover_sim (laptop path
`/workspace/ros2_ws/src/rover_sim`); `docs/INTERFACE.md` is its contract.

**The two bodies do NOT agree, and that is the trap.** The sim has a `map`
frame (slam_toolbox) and takes TwistStamped; the real rover has **no map frame
at all** and takes plain Twist. Code that works in sim can therefore fail
silently on the rover — which is exactly how `navigate_to_pose` was broken for
months. `ROS2Bridge` handles the cmd_vel shape (`robot_body`), and `NAV_FRAME`
handles the frame; anything else that differs is on you to check.

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
