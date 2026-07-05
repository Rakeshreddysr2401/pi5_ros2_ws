# LangRobo Pi5 brain — project guide

Home robot "Rakhi": Pi5 (this repo) runs the LangGraph brain; Jetson Orin runs
STT/TTS/camera/YOLO (separate `speech_vision` repo); Mac Mini serves the LLM
(llama.cpp, `singireddys-mac-mini.local:8080`); ESP32 drives the wheels.
Read HOW_IT_WORKS.md for the end-to-end walkthrough (boot, turn lifecycle,
failure paths); ARCHITECTURE.md before touching graph/agent code;
OPERATIONS.md for run/deploy/troubleshooting; PRODUCT.md for the roadmap.

## Commands

```bash
# Test (pure core — no robot, no LLM, no keys; ~4s)
cd src/langrobo_core && python3 -m pytest tests/ -q

# Build + deploy after code changes
colcon build --symlink-install && sudo systemctl restart langrobo-brain

# Run modes (NEVER two at once — both drive /cmd_vel + UDP 8888)
systemctl status langrobo-brain langrobo-microros   # production (systemd)
ros2 launch langrobo_ros brain_launch.py            # foreground all-in-one
./scripts/dev.sh                                    # LangGraph Studio :2024

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
- `langrobo_core/prompts.py` — EVERY system prompt (agents + background jobs);
  agents append only dynamic blocks (household, now-playing, date) in-module
- `langrobo_core/agents/` — one module per agent (node fn + context assembly)
- `langrobo_core/tools/` — @tool functions; per-agent sets in `__init__.py`;
  robot I/O via `_bridge.get()`
- `langrobo_core/services/` — config (validated .env), llm (slots + fallback),
  memory (embedded Qdrant + fastembed), consolidation (nightly episodic→facts,
  local model only), watch (armed person-detection alerts → Telegram),
  telegram (channel: long-poll + sends),
  permissions (role→capability policy — enforced in tools, never only prompts),
  health (FastAPI :8090), logging, metrics
- `langrobo_ros/` — agent_node (params, queues, worker loop, cache warmer),
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

Passwordless SSH: `ssh rakhi24@192.168.2.20`. The speech_vision repo is at
`~/robot` on the Jetson (branch dev-1.0.4_voice_upgrade+) and has its own
CLAUDE.md + VOICE_PIPELINE.md — read those before editing; they document the
container build/restart procedure and five hard-won gotchas (venv-python
colcon builds, zombie launch children, pinned pip index, broken torchaudio,
ec_speaker audio routing). Workflow: edit via ssh/rsync on the host paths
(`~/robot/ai_ws` is bind-mounted into the `ai_stack` container), build and
restart via `docker exec`, test over ROS2 topics from this machine, commit in
`~/robot` over ssh. The `/voice/*` + `/audio/*` topic contract is shared —
change both repos together or neither.

## Gotchas

- Nav2/SLAM don't exist yet (phase 2): `navigate_to_pose` reports honestly
  after a 10s server wait. The interfaces are the reserved slot — keep them.
- `strict_tool_calls` + streaming need the llama.cpp server started with
  `--jinja --parallel 4`.
- Smoke tests import `tools/swiggy_mcp.py` which probes the network only when
  SWIGGY_ACCESS_TOKEN is set.
- Pi5↔Jetson clocks drift ~1.5s (chrony peering pending) — latency_replay
  flags negative deltas.
