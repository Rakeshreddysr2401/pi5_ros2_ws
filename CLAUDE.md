# LangRobo Pi5 brain — project guide

Home robot "Mitra" (renamed from "Rakhi" 2026-09-20; the `rakhi24` username and `rakhi-jetson` hostname are unrelated and unchanged). Four machines:

- **Pi 5** (this repo) — the LangGraph brain, **three agents**, plus a CPU-only
  STT/TTS pair (`pi5_voice_pkg`) so voice runs concurrently with driving.
- **Jetson Orin** — perception and motion control: D555 + RPLidar C1,
  cuVSLAM + lidar odometry fused into `/odom`, slam_toolbox, nvblox, Nav2,
  `goal_exec` (exact turns/drives) + `reach` (nav2 + exact finish), and the
  phase-4 VLM bridge (`image_bridge` publishes the colour frame as JPEG for
  `look()`; `pixel_to_goal` turns a VLM-picked pixel into an odom-frame goal).
  Repo: **`~/rover`** on the Jetson (container `rover`), brought up with
  `./rover up`. (`~/langrobo_perception` and `~/robot` are the old stacks.)
- **Mac Mini** — the LLM and VLM (llama.cpp, `singireddys-mac-mini.local:8080`).
  **Must run with `--jinja --parallel 4`** — one KV slot per agent, plus slot 3 for the photo survey.
- **ESP32** — 50 Hz closed-loop PID on four wheels, micro-ROS over WiFi.

**FIND_AND_GO.md is the priority flow** ("what do you see?" … "go near it":
photo-grounded object positions, go-to-the-spot, search) — read it before
touching approach/look/survey/photos or the Jetson's pixel_to_goal/reach.
Read HOW_IT_WORKS.md for the end-to-end walkthrough (boot, turn lifecycle,
failure paths); **ARCHITECTURE_LLD.md before touching graph/agent code**;
INTEGRATION_GAPS.md before building anything that touches the world;
OPERATIONS.md for run/deploy/troubleshooting; PI5_VOICE.md for the Pi5 voice trio
(speaker/mic owner + STT + TTS); **VOICE_ROADMAP.md** for the phased voice plan
(what is done, what is next); WAKE_WORD_INTEGRATION.md to train and plug in the
"Mitra" wake word.

## Fleet start — one command brings up the whole robot

`scripts/fleet.sh {sim|rover|stop|down|status}` (run here on the Pi5). Picks the robot **body**:

- **`rover`** — the REAL body: starts this Pi5's micro-ROS agent (ESP32 wheels)
  and, if the Jetson stack is not already up, runs `./rover up` there (every
  layer in order with its own PASS/FAIL gate — camera, lidar, pose, fused,
  slam, map, nav, vlm — then Studio on this Pi5 and RViz on the laptop;
  ~10 min from cold). Voice is the Pi5's own trio (`langrobo-voice` user
  unit, starts at boot). Switches `robot_body` to `rover` (plain Twist on
  `/cmd_vel`).
- **`sim`** — the SIMULATION body: sshes the laptop and starts its Gazebo sim +
  Nav2 (`rover_sim`); voice stays on the Pi5; the Jetson is not used (its old
  sim roles, `ai_stack` + `isaac_ros`, are retired). Switches `robot_body` to
  `sim` (TwistStamped on /mecanum_drive_controller/cmd_vel).
- **`stop`** parks the robot: stops the body (sim + the Jetson's `rover` container) but
  keeps `langrobo-brain` and voice up, so chat/Telegram keeps listening. No password.
- **`down`** full shutdown: everything `stop` does PLUS this Pi5's system units (brain,
  micro-ROS, discovery) via sudo. Those units are `enabled`, so a Pi5 reboot restarts them.
- **`status`** shows who's up everywhere, per Jetson layer.

**After a power cycle** the Pi5 units come back on their own; the Jetson's container
does not. `./scripts/fleet.sh rover` (or `ssh rakhi-jetson.local 'cd ~/rover && ./rover up'`)
is the whole recovery. The Jetson's own STARTUP.md is the layer-by-layer version.

Each machine can still be driven on its own — the laptop via `rover_sim/.../fleet_sim.sh`,
the Jetson via `~/rover/rover <layer>`. Reaches the other machines by mDNS name over
passwordless ssh; DDS is plain multicast on domain 0 everywhere (NETWORKING.md).

## Commands

```bash
# Whole robot (see "Fleet start" above)
./scripts/fleet.sh rover      # real body (pi5 microros + jetson ./rover up; pi5 voice)
./scripts/fleet.sh sim        # simulation body (laptop sim; pi5 voice)
./scripts/fleet.sh stop       # park robot body (brain stays up)
./scripts/fleet.sh down       # full shutdown incl. Pi5 services (sudo)
./scripts/fleet.sh status

# Test (pure core — no robot, no LLM server, no keys; ~300 tests, ~10s)
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
   `--parallel 4` (slot 3 is the background photo survey's): each agent's
   ~900-token prompt prefix then stays resident in its own cache. Two agents on one slot evict each other every turn
   (~18-50s of re-prefill). agent_node probes the server's real slot count at
   boot, wraps with modulo, and warns loudly if it had to.
   **Separate slots are not enough on their own:** as of 2026-09-27 the Mac's
   server wipes every other slot's cache when one is used (Gemma's
   sliding-window cache; likely fix `--swa-full`). `scripts/llm_cache_check.py`
   is the PASS/FAIL test — run it after any change to the server's flags.
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
- **There is no fast path.** `fastpath.py` (two regex lanes in front of the
  graph) was removed 2026-09-08 — its intent vocabulary was a hand-kept lookup
  table that had already drifted (it gated objects on a COCO class list while
  dispatching to a VLM tool). Every turn now goes through the graph, so
  movement costs 2 LLM calls and a vision question 3. **Do not reintroduce
  regex intent matching** — the replacement is a MiniLM entry classifier,
  designed in INTENT_ROUTING_PLAN.md and not yet built.
  Stopping never depended on it: `agent_node._on_user_input` halts the wheels
  on every utterance before the graph runs.
- `langrobo_core/graph/` — topology (build.py, derived entirely from
  registry.py), entry routing (sticky agent, else chat), handover + loop guards
- `langrobo_core/agents/` — `factory.py` builds EVERY agent from its spec;
  there are no hand-written nodes
- **Every photo becomes object memory** (`tools/survey.py`, 2026-09-27): look(),
  each search view and locate_object hold the photo's depth + camera pose at the
  Jetson (`hold_frame`, 24 kept) and queue it; in the background — only while no
  turn or search is running, on llama.cpp slot 3, unstreamed — the VLM lists the
  objects and the Jetson places each one using THAT photo's pose. So "go to the
  chair" later is worked out from where the robot is now (approach.py step 1).
  The search itself is 8 views, 45° apart (90° steps missed objects at the seams).
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
- `pi5_voice_pkg/` — the Pi5 voice trio: `audio_device_node` is the ONE
  owner of the speaker + mic (any paired Bluetooth device, or a wired
  fallback; publishes latched `/voice/audio_ready`), `stt_node` and
  `tts_node` follow it and never touch Bluetooth. `bt_audio.py` is the
  pure bluez/PipeWire glue. `/bt-audio` (Claude skill) is the operator
  checklist. Robot name / wake word: **Mitra**. The acoustic gate is
  **OFF since 2026-09-26** (owner wants a better-trained model first):
  `wake_detector: transcript_alias` + `require_wake: true`, so everything is
  transcribed but only text containing "mitra" / "hey mitra" becomes a turn.
  The trained `models/wake/mitra.onnx` (and Telugu `rakhi.onnx`) are in git,
  one `./scripts/wake_switch.py mitra` away. It answers "చెప్పండి బాస్" when
  you pause after the name (`wake_cue.py`).
  `./scripts/wake_switch.py` swaps model/threshold and restarts the service;
  `./scripts/wake_test.sh` shows a live score bar. WAKE_WORD_INTEGRATION.md
  is the train-and-deploy recipe.

## Config split

- `src/langrobo_ros/config/agent_params.yaml` — LLM provider/model, robot_body
  (ROS params). KV slots are in `registry.py`; named places only come from
  `save_location` (no configured defaults — odom restarts at every boot)
- `.env` (validated fail-fast at startup) — keys + LANGROBO_* service settings;
  full table in OPERATIONS.md; template in example.env
- Robot state lives in `~/.langrobo/` (locations.json, telegram_offset,
  telegram_deferred.json)

## Working on the Jetson from here

Passwordless SSH: `ssh rakhi24@rakhi-jetson.local`. The live repo is **`~/rover`**
(branch `rover-v1.1.2-refactor`, container `rover`, image `orin-nav:1.1`); read its
README.md, STARTUP.md (power-on → working), OPERATIONS.md and OPEN_ISSUES.md before
editing. Its rules: the image has no Dockerfile and must never be modified; nodes are
host files bind-mounted read-only, so edit on the host and restart the layer
(`./rover <layer>`); compiled packages are built INSIDE the image with `--user $(id -u)`.
Test over ROS 2 topics from this machine, commit in `~/rover` over ssh.

The topic contract between the repos is in INTEGRATION_GAPS.md (brain side) and
`~/rover/phase4` (Jetson side: `/vision/pixel_query` → `/vision/pixel_result`,
`/goal_exec/*`, `/reach/*`, `/camera/color/image_raw/compressed`) — change both
together or neither. `~/robot` (the old voice stack, `ai_stack`) and
`~/langrobo_perception` (the old `isaac_ros` stack) are retired; their containers
no longer exist.

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
  `--jinja --parallel 4` (one slot per agent: chat/local_agent/navigate, + 3 for the photo survey).
- Pi5↔Jetson clocks drift ~1.5s (chrony peering pending) — latency_replay
  flags negative deltas.
- **A prompt rule the model has to follow is not a fix — it is a thing to
  measure.** CHAT_PROMPT and NAVIGATE_PROMPT both said, in as many words, "a
  vision question is not yours to answer, hand it to local_agent." The 12B
  model ignored that instruction on real hardware, twice, in two different
  shapes (spoke directly with no tool call; then, once that was caught,
  proposed `scan_surroundings()` — a real 360° rotation — instead of handing
  over). The actual fix is `graph/build.py`'s vision-question backstop
  (ARCHITECTURE_LLD.md §3.6): a deterministic check on the graph, matched
  against the user's own words, not another sentence in a prompt. Any new
  "agent X must never do Y" rule is the same shape of untrustworthy until
  it's been driven on the robot, not just read.

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

- The sim is on the same plain-multicast DDS graph (domain 0, no
  `ROS_DISCOVERY_SERVER`), so this brain's `navigate_to_pose` tool talks to
  the sim's Nav2 server directly.
- Highlights of the contract: `/navigate_to_pose` (NavigateToPose),
  `/scan` 10 Hz, `/cam_1/color/image_raw` + `/cam_1/depth/image_rect_raw`
  15 Hz (D555 names — nvblox-ready), odom `/mecanum_drive_controller/odom`,
  cmd_vel is **TwistStamped** on `/mecanum_drive_controller/cmd_vel`
  (plain `/cmd_vel` exists only while its Nav2 is up).
- Sim runs ≈0.1× real time in the furnished house world (laptop iGPU) — don't
  tune wall-clock timeouts against it.
- Keep the machine/interface details in sync across this CLAUDE.md, the
  Jetson's `~/rover` docs, and rover_sim's — change all or none.
