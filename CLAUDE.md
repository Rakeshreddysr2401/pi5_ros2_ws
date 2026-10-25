# LangRobo Pi5 brain — project guide

Home robot "Kino" (renamed from "Rakhi" 2026-09-20; the `rakhi24` username and `rakhi-jetson` hostname are unrelated and unchanged). Four machines:

- **Pi 5** (this repo) — the LangGraph brain, **three agents**, plus a CPU-only
  STT/TTS pair (`pi5_voice_pkg`) so voice runs concurrently with driving.
- **Jetson Orin** — perception and motion control: D555 + RPLidar C1,
  cuVSLAM + lidar odometry fused into `/odom`, slam_toolbox, nvblox, Nav2,
  `goal_exec` (exact turns/drives) + `reach` (nav2 + exact finish), and the
  phase-4 VLM bridge (`image_bridge` publishes the colour frame as JPEG for
  `look()`; `pixel_to_goal` turns a VLM-picked pixel into an odom-frame goal),
  and "follow me" (`./rover follow`: YOLO person tracker -> `/vision/people`,
  `follow_node` on `/follow/*`; in `./rover up` since 2026-10-11, INTEGRATION_GAPS.md §8;
  since 2026-10-13 it turns to FIND the person, remembers their clothes' colours and
  searches where they went when lost).
  Repo: **`~/rover`** on the Jetson (container `rover`), brought up with
  `./rover up`. (`~/langrobo_perception` and `~/robot` are the old stacks.)
- **Mac Mini** — the LLM and VLM (llama.cpp, `singireddys-mac-mini.local:8080`).
  `LANGROBO_LLM` in .env picks the server (services/llm_profiles.py): `gemma` = the
  Mac (default), `qwen` = Qwen3.5-9B on the laptop's RTX 4050 (`~/llama.cpp-bin/qwen9b.sh`,
  only while the twin is down). Measured 2026-10-20 on the brain's prompts: Gemma 16/16
  tools right, Qwen 13/16 -- the owner keeps Gemma.
  **Start it with `e12b5`** in a Mac terminal after every power-on (nothing autostarts; it asks the
  Mac password once to raise the GPU memory limit to 13 GB): a PATCHED llama.cpp b9830 with
  `--jinja --parallel 5`, 5 x 16384 -- one KV slot per agent + photo questions (`ask_photos`) +
  the voice "is this for me?" check (unused since that filter went off, 2026-10-12). `scripts/mac_mini/e12b5`, docs/mac/LLAMA_CACHE_ISSUE.md §8.
- **ESP32** — 50 Hz closed-loop PID on four wheels, micro-ROS over USB serial to the Pi 5
  (`/dev/ttyUSB0`, since 2026-10-07; firmware `UROS_SERIAL` switch, `run_microros.sh udp` = old WiFi link).

**FIND_AND_GO.md is the priority flow** ("what do you see?" … "go near it":
photo-grounded object positions, go-to-the-spot, search) — read it before
touching approach/look/survey/photos or the Jetson's pixel_to_goal/reach.
Read HOW_IT_WORKS.md for the end-to-end walkthrough (boot, turn lifecycle,
failure paths); **ARCHITECTURE_LLD.md before touching graph/agent code**;
INTEGRATION_GAPS.md before building anything that touches the world;
OPERATIONS.md for run/deploy/troubleshooting; docs/voice/PI5_VOICE.md for the Pi5 voice trio
(speaker/mic owner + STT + TTS); **docs/voice/VOICE_ROADMAP.md** for the phased voice plan
(what is done, what is next); docs/voice/WAKE_WORD_INTEGRATION.md to train and plug in the
"Kino" wake word; docs/voice/MAC_STT.md for `stt_provider: apple` (Apple's on-device
recogniser served from the Mac Mini, `scripts/mac_mini/stt_server`, :8091 -- English only).

## Fleet start — one command brings up the whole robot

`scripts/fleet.sh [rover|stop|down|status|check|sim|twin]` (run here on the Pi5). Picks the robot **body**.
**The real rover is the default** (owner, 2026-10-11: it is the goal; the sims are used rarely):
`./scripts/fleet.sh` with no argument (or `up` / `start`) is `rover`. Start `sim` or `twin` ONLY
when the owner explicitly asks for the virtual rover, and take it down after (`fleet.sh twin down`).

- **`rover`** — the REAL body: starts this Pi5's micro-ROS agent (ESP32 wheels)
  and, if the Jetson stack is not already up, runs `./rover up` there (every
  layer in order with its own PASS/FAIL gate — camera, lidar, pose, fused,
  slam, map, nav, vlm — then Studio on this Pi5 and RViz on the laptop;
  ~10 min from cold). Voice is the Pi5's own trio (`langrobo-voice` user
  unit, starts at boot).
- **`twin up|status|down`** — the SIMULATED robot (Isaac Sim on the laptop, "The twin"
  below): after `./sim up home_real` on the laptop, runs `TWIN=1 ./rover up` on the
  Jetson and the twin brain here, all on domain 42.
- **`stop`** parks the robot: stops the body (the Jetson's `rover` container) but
  keeps `langrobo-brain` and voice up, so chat/Telegram keeps listening. No password.
- **`down`** full shutdown: everything `stop` does PLUS this Pi5's system units (brain,
  micro-ROS) via sudo. Those units are `enabled`, so a Pi5 reboot restarts them.
  (The Fast DDS discovery unit is gone, 2026-10-18 — the fleet is plain multicast;
  re-running `scripts/install_systemd.sh` disables and removes an old install.)
- **`status`** shows who's up everywhere, per Jetson layer.

**After a power cycle** the Pi5 units come back on their own; the Jetson's container
does not. `./scripts/fleet.sh rover` (or `ssh rakhi-jetson.local 'cd ~/rover && ./rover up'`)
is the whole recovery. The Jetson's own STARTUP.md is the layer-by-layer version.

Each machine can still be driven on its own — the laptop's twin via `~/kino_sim/kino`,
the Jetson via `~/rover/rover <layer>`. Reaches the other machines by mDNS name over
passwordless ssh; DDS is plain multicast on domain 0 everywhere (NETWORKING.md).

## Commands

```bash
# Whole robot (see "Fleet start" above)
./scripts/fleet.sh            # = rover, the default: real body (pi5 microros + jetson ./rover up; pi5 voice)
./scripts/fleet.sh rover      # real body (pi5 microros + jetson ./rover up; pi5 voice)
./scripts/fleet.sh twin up    # the simulated robot (after ./sim up on the laptop; domain 42)
./scripts/fleet.sh stop       # park robot body (brain stays up)
./scripts/fleet.sh down       # full shutdown incl. Pi5 services (sudo)
./scripts/fleet.sh status
./scripts/llm_switch.sh gemma|qwen   # which LLM server: Mac Gemma 4 12B (default) | laptop Qwen3.5-9B

# Test (no robot, no LLM server, no keys; never traces) — run all three after a change
cd src/langrobo_core && python3 -m pytest tests/ -q     # pure core, ~620 tests, ~25 s
cd src/pi5_voice_pkg && python3 -m pytest tests/ -q     # voice, ~135 tests
cd src/langrobo_ros && python3 -m pytest test/ -q       # bridge (needs rclpy), ~10 tests

# Build + deploy after code changes
./scripts/build_ws.sh && sudo systemctl restart langrobo-brain

# Run modes (NEVER two at once — both drive /cmd_vel + the ESP32's serial port)
systemctl status langrobo-brain langrobo-microros   # production (systemd)
ros2 launch langrobo_ros brain_launch.py            # foreground all-in-one
./scripts/dev.sh                                    # LangGraph Studio :2024 (draws the graph)

# Observe
journalctl -u langrobo-brain -f -o cat              # JSON logs (jq-able, trace_id per turn)
curl -s localhost:8090/status | jq                  # LLM health, telegram, turn state
python3 scripts/latency_replay.py "utterance"       # per-stage latency waterfall
python3 scripts/gemma_flow_eval.py                   # real LLM picks the right tools/handovers? (slot 4; 41/41 on 2026-10-21) -- after any prompt/tool-doc change

# Python deps (system python, PEP 668)
pip3 install --break-system-packages -r requirements.txt
```

## Hard rules

1. **`src/langrobo_core` must never import rclpy** (pure zone — pip package).
   Only `src/langrobo_ros` (agent_node.py, ros2_bridge.py) touches ROS2. Tools
   that need ROS message types import them lazily *inside* the function body.
2. **One llama.cpp KV slot per agent.** Slots are declared in `registry.py`
   (`AgentSpec.slot`), NOT as ROS params. The server runs
   `--parallel 5` (`e12b5`, since 2026-10-11): slots 0-2 are the agents, slot 3 the **vision-tool slot**
   (photo questions -- `ask_photos`, search views, locate, the arrival check --
   kept OFF local_agent's slot 1, which holds the vision conversation and its
   earlier photos; `tools/survey.py` VISION_TOOL_SLOT, asserted in
   registry.py). On a smaller server locate falls back to slot 1. **Slot 4 is the voice addressing filter's** ("is this for me?", `pi5_voice_pkg/relevance.py`; idle since the filter went off 2026-10-12 -- kept for when it comes back): unpinned it landed in slot 3 and threw the photo cache out on every TV sentence (2026-10-10), so the Mac runs `--parallel 5`; stt_node pins slot 4 only when /props shows it, and warns otherwise. Each
   agent's prompt prefix then stays resident in its own cache. Two agents on
   one slot evict each other every turn (~18-50s of re-prefill). agent_node
   probes the server's real slot count at boot, wraps with modulo, and warns
   loudly if it had to. `scripts/llm_cache_check.py` is the PASS/FAIL test --
   run it after any change to the server's flags (PASS since 2026-10-01, and
   `LANGROBO_WARM_ALL=1` keeps every agent warm while idle).
   **The Mac is not `--swa-full`, so its cache has a REWIND LIMIT:** a request
   that shares only a prefix of a slot's cached prompt keeps the cache if it
   drops <= ~480 tokens, and re-reads everything (~25 s for 3k tokens) if it
   drops more (measured 2026-10-02 at ~450, 2026-10-11 at >= 482 with `-ub 512`;
   the margin is the batch size, so never lower `-ub`: 256 cut it to ~160). The
   cache check only tests extending, so it still PASSES. `--swa-full` would lift
   it but needs ~20 GB of cache for 5 slots on Gemma 4 -- it can't fit the 16 GB Mac.
   Prompts SHORTER than 1024 tokens used to miss the cache on every request (a
   llama.cpp bug, patched in our build since 2026-10-11 -- LLAMA_CACHE_ISSUE.md). Anything that branches off a cached prefix must stay under
   it -- `tools/photo_recall.py` THE REWIND LIMIT is the worked example.
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
   neither. The one other spoken path is CODE, not the model: during a long
   search `approach._say` publishes a fixed progress line ("I don't see the X
   from here, so I'm looking around") as a COMPLETE utterance -- never an open
   one, which would mute the mic for the whole search. And since 2026-10-12
   `utils/tool_speech.py`: agent_node says a line as a slow tool is CALLED
   ("Looking for the bag.") and a plain success as it RETURNS ("Turned left 90
   degrees."), inside the turn's utterance; the model's next run, which would
   only restate it, is muted (one run only -- a second step is still heard).
   The one exception for a MODEL: `say_in_room` (tools/room_voice.py, 2026-10-20),
   chat only -- a Telegram sender's words said aloud ("tell her loudly"); a
   Telegram reply never reaches the speaker. It refuses on a voice turn.
   An agent may also answer exactly `[silent]` (SPEECH_STYLE): not said to it
   (TV, room talk); never spoken, nothing kept -- unless the name was said.
   That is a prompt rule, so it is measured (2026-10-13, chat's real prompt: silent
   for 3/6 room sentences) and backed by code: `utils/addressing.py` marks a voice
   turn with no name and no robot reply in the last 20 s as NOT addressed, and the
   side-effect tools (Telegram sends, memory remember/forget) refuse on it.
5. **Missing keys degrade, never crash**: no Tavily key → no web search and
   chat says it can't look that up; no Telegram allowlist → the channel stays
   off; Mac Mini down → cloud fallback or a spoken offline message. Keep this
   property when adding features.
6. New proactive behaviour = a producer injecting `[SYSTEM]` turns into
   agent_node's system queue (the Nav2 arrival report is the worked
   example) — don't invent new mechanisms.
   A report that is only a FACT (a drive ended, a background search found or
   missed its object) is a notice instead (services/notices.py, 2026-10-20):
   delivered as written by code -- spoken, or sent to the asker's Telegram
   with the arrival photo -- and kept in the history; no LLM turn. Only a drive
   with an errand (`then`) still becomes a [SYSTEM] turn. approach_described_object
   returns in seconds and searches in a thread; asked again for the same object
   it adds the errand to the drive under way instead of restarting it.

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
  regex intent matching** — the replacement is an entry classifier, and it is
  BUILT and ON in production: `langrobo_core/routing/` (bge-small embeddings
  over each spec's `examples` + `intent_examples`, ~15 ms on the Pi, a
  confident pick enters that agent directly; `LANGROBO_INTENT_ROUTING=off|shadow|on`,
  `.env` has `on`). The cloud alternative `services/jev.py` (TypeSafe's Jev,
  `LANGROBO_JEV`) is built and off. Stopping never depended on either:
  `agent_node._on_user_input` halts the wheels before the graph runs (while
  the robot moves, only a stop word does — `utils/stop_words.py`).
- `langrobo_core/graph/` — topology (build.py, derived entirely from
  registry.py), entry routing (sticky agent, else chat), handover + loop guards
- `langrobo_core/agents/` — `factory.py` builds EVERY agent from its spec;
  there are no hand-written nodes
- **The robot's memory is its photos** (`tools/photos.py` + `tools/photo_recall.py`,
  2026-10-02; owner: "don't save like 'bag at x,y'"). Every photo -- look(), each
  search/scan view, locate, the confirm look -- is logged (number, image, time,
  pose; 24 kept, the Jetson holds each one's depth). `ask_photos(question)` (all
  three agents) asks the VLM over ALL of them in one request on slot 3, then
  re-asks that one photo for a tight box; the Jetson places it with THAT photo's
  depth + pose; distance/bearing come from the pose now. "go near X" asks the
  photos first, then faces / drives over / confirms (approach.py step 1).
  The log is append-only and drops 8 at a time so the Mac keeps the photos
  cached (~4-5 s a question warm); what follows the photos must stay < ~420
  tokens -- the Mac is not `--swa-full` (it can't fit) and loses the cache on a longer rewind.
  The old background object list (`tools/survey.py`) is OFF
  (`LANGROBO_PHOTO_SURVEY=1`). The search is 8 views, 45 deg apart.
  **Errands and arrival** (2026-10-02): the drive tools take `then` ("go to the
  box and tell me what is on it"), carried to the [SYSTEM] arrival report by
  `movement.arrival_task_note` (done on arrival / "NOT done" on failure /
  dropped on cancel); and on arrival code checks the object is really there
  (`approach.arrival_check`, one fresh photo). The arrival turn acts as the
  person who asked (Telegram name + role). FIND_AND_GO.md has the flow.
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
- `pi5_voice_pkg/` — the Pi5 voice trio. **Since 2026-10-10:** mic = USB
  AM-C28 array (audio passed to the recogniser untouched — owner's rule; Silero
  VAD only marks start/end), speaker = boAt Stone (A2DP); **STT = Parakeet on
  the Pi** (`stt_provider: sherpa`, owner's switch 2026-10-10 — English only,
  offline; `sarvam` = Telugu->English cloud, one line away), TTS = Sarvam
  streamed in Telugu (`sarvam_stream`, fallback `piper`). Measurements and
  the owner's-voice scoreboard: docs/voice/VOICE_LAB_2026-10.md (re-score any
  change with `scripts/voice_lab/score_set.py`, not by ear). `audio_device_node` is the ONE
  owner of the speaker + mic (any paired Bluetooth device, or a wired
  fallback; publishes latched `/voice/audio_ready`), `stt_node` and
  `tts_node` follow it and never touch Bluetooth. `bt_audio.py` is the
  pure bluez/PipeWire glue. `/bt-audio` (Claude skill) is the operator
  checklist. Robot name / wake word: **Kino**. The acoustic gate is
  **OFF since 2026-09-26** (owner wants a better-trained model first):
  `wake_detector: transcript_alias` + `require_wake: true`, so everything is
  transcribed; text containing "kino" / "hey kino" becomes a turn at once and,
  with `addressing_mode: llm` (2026-10-04), any other sentence is put to the
  brain's LLM as "is this for me?". **OFF since 2026-10-12** (owner: every sentence
  is processed, the agent clarifies or answers `[silent]`): `require_wake: false`;
  sentences without the name wait while a turn runs (`HoldWhileBusy`). The wake word itself is undecided (owner,
  2026-10-10: Kino or another name).
  The trained `models/wake/kino.onnx` (and Telugu `rakhi.onnx`) are in git,
  one `./scripts/wake_switch.py kino` away. It answers "చెప్పండి బాస్" when
  you pause after the name (`wake_cue.py`).
  `./scripts/wake_switch.py` swaps model/threshold and restarts the service;
  `./scripts/wake_test.sh` shows a live score bar. docs/voice/WAKE_WORD_INTEGRATION.md
  is the train-and-deploy recipe.

## Config split

- `src/langrobo_ros/config/agent_params.yaml` — LLM provider/model
  (ROS params). KV slots are in `registry.py`; named places only come from
  `save_location` (no configured defaults — odom restarts at every boot)
- `.env` (validated fail-fast at startup) — keys + LANGROBO_* service settings;
  full table in OPERATIONS.md; template in example.env
- Robot state lives in `~/.langrobo/` (locations.json, telegram_offset,
  telegram_deferred.json)

## Working on the Jetson from here

Passwordless SSH: `ssh rakhi24@rakhi-jetson.local`. The live repo is **`~/rover`**
(branch `rover-v1.1.16-review`, container `rover`, image `orin-nav:1.2` — Isaac ROS 4.6
cuVSLAM, `VO=isaac`; THE image by the owner's decision 2026-10-18, robot and twin; the first
real-robot run is ISAAC_ROS_4_6_UPGRADE.md step F; `IMAGE=orin-nav:1.1 ./rover up` is the
rollback, kept until it passes); read its
README.md, STARTUP.md (power-on → working), OPERATIONS.md and OPEN_ISSUES.md before
editing. Its rules: the image has no Dockerfile and must never be modified; nodes are
host files bind-mounted read-only, so edit on the host and restart the layer
(`./rover <layer>`); compiled packages are built INSIDE the image with `--user $(id -u)`.
Test over ROS 2 topics from this machine, commit in `~/rover` over ssh.
Close quarters (2026-10-03): nvblox gets depth only while the rover is not
turning (`phase2/nodes/depth_gate.py`); goal_exec / reach / pass judge
obstacles on nvblox's fused map, not single depth frames
(`phase3/nodes/fused_obstacles.py`, `ROVER_OBSTACLES=depth` reverts); nav2
routes keep 4 cm, tighter gaps go to reach's look + straight pass.
Since 2026-10-04 that map is `/ghost_clear/grid`: nvblox with the marks of
moved things erased (a mark the LiDAR also saw, then sees through -- people;
low things stay; `phase2/nodes/ghost_clear.py`). And on "no path" reach drives
back along its own trail (`phase3/nodes/trail.py`); the same failure 3 times
from one spot ends the goal, with a `note` saying why the way back failed.

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
  to within 4-5 cm. Bring it up with `./scripts/fleet.sh rover` (the Jetson's
  `./rover up`).
- **Feature flags change what the model is shown.** `LANGROBO_FOLLOW=1` binds
  `follow_person` to navigate (and adds "follow me" to the routing copy);
  `LANGROBO_PAN_TILT=1` binds `point_camera`. Production `.env` has FOLLOW on,
  PAN_TILT off. Off, the schema is not shipped and the prompts are byte-identical.
- **Everything is `odom`, never `map`.** That Nav2 runs single-session with no
  relocalisation, so no node publishes a map frame at all. Goals, TF pose
  reads and any object positions must agree on `ROS2Bridge.NAV_FRAME`
  (default `odom`, one constant, `LANGROBO_NAV_FRAME` to override). A `map`
  goal is not an error — it is silently untransformable, which is how every
  `navigate_to_pose` call failed for months.
- **Three topics this brain used to speak have no listener on the rover** and
  the tools built on them were removed (or, for the servos, bound only behind
  `LANGROBO_PAN_TILT=1`) rather than left looking functional:
  `/vision/detections_3d` (object positions — the contract to restore it is in
  INTEGRATION_GAPS.md §1), `/vision/target*` (YOLO servo loop), `/servo_pan` +
  `/servo_tilt` (no mount, and the ESP32 firmware has three subscriptions and
  none are servos). (`/audio/music_*` was the fourth; since 2026-10-04 the
  Pi's own `media_node` answers it.) Check for a subscriber before building
  on a topic here.
- Streaming tool calls need the llama.cpp server started with
  `--jinja --parallel 5` (one slot per agent: chat/local_agent/navigate, + the vision-tool slot 3,
  + the voice filter's slot 4) -- `e12b5` on the Mac does it.
- **`.env` is loaded before any `langrobo_core` import** (top of agent_node.py).
  Some settings are read when a module is imported (`tools/web.py` builds
  `tavily_search` only if `TAVILY_API_KEY` is set then): loaded later, web
  search was silently off in every systemd run until 2026-10-02.
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

## The twin (kino_sim) — test the real robot's code on a simulated robot

The twin is the robot's HARDWARE simulated, nothing else: since 2026-10-18 in
**Isaac Sim 6.2** (5.1 retired 2026-10-18) on the new laptop (user `chotesh`, 192.168.1.9, DHCP, RTX
4050). Repo github.com/Rakeshreddysr2401/kino_sim (private),
laptop `~/kino_sim` (branch `main`). The laptop simulates the D555, the C1
and the ESP32 + encoders with the rover's measured body, noise and wheel
response; the Jetson runs the rover's WHOLE, unchanged stack on it
(`TWIN=1 ./rover up`: cuVSLAM, fusion2, slam_toolbox, nvblox, nav2, reach,
the VLM bridge); this Pi runs a second brain (below). Everything on **ROS
domain 42**, so the twin can never reach the real wheels (domain 0).

**Switching body is the domain, not the code:** a fix found on the twin is
made in the real code (`~/rover` on the Jetson, this repo) and already is the
robot's. Real robot: `fleet.sh rover`. Twin: on the laptop `./sim up
home_real`, then here `./scripts/fleet.sh twin up` (status / down). The twin
announces itself on domain 42 (latched `/sim/hardware`: the laptop's address,
the cable ports, the world, the body's params sha1), so neither this Pi nor
the Jetson logs in to the laptop. The Jetson runs one stack at a time (real or
twin): it is CPU-bound.

Laptop commands (in `~/kino_sim`): `./sim up <world> [follow=true]` (the
Isaac window chasing the rover), `./sim status`, `./sim scenario <world>`
(drives the world's goals, judged on the TRUE pose: a touch fails the run),
`./sim selftest`, `./sim parity snapshot real|twin` + `compare` (does the
twin publish the real robot's interface?), `./sim rviz [real]`. Results,
fixes and open items: kino_sim `docs/STATUS.md`; agent rules: its `CLAUDE.md`.
First Isaac day (2026-10-18): every `./rover up` gate OK; home_real scenario
4/4 reached, through the 80 cm door; it drives over the 1 cm tablet, as the
real robot does.

**The brain on the twin:** `scripts/twin_brain.sh up` runs a SECOND
agent_node, unchanged, on domain 42 beside the real one (langrobo-brain,
domain 0, untouched): `LANGROBO_STATE_DIR=~/.langrobo_twin` (its photos, saved
places and object memory never mix with the real robot's), Telegram off, no
voice, its own teleop switch (`twin_brain.sh manual on|off`, a file), health on
:8092, LangSmith project `kino-twin`. Talk to it with `twin_brain.sh say "..."`
(prints the replies and the later arrival report). The Jetson half runs the
rover's own `pixel_to_goal`, so "go near X" is the real path. It shares the Mac
mini's llama.cpp slots with the real brain. **Default is the real robot**: the
twin runs only when started. The brain publishes the same plain Twist on
`/cmd_vel` to both (the twin's ESP32 stand-in, `esp32_twin`, takes it as the
ESP32 does).
