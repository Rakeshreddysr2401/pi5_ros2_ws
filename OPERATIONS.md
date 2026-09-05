
# LangRobo — Operations

Deploy, run, observe, and troubleshoot the Pi5 brain.

---

## Run modes

| Mode | Command | Use |
|---|---|---|
| **Production** | `./scripts/install_systemd.sh` (once) | 24/7: auto-restart, boot persistence, JSON logs in journald |
| Foreground | `ros2 launch langrobo_ros brain_launch.py` | attended testing (micro-ROS included) |
| Dev / Studio | `./scripts/dev.sh` | LangGraph Studio UI + micro-ROS agent |
| Dev + voice | `./scripts/dev_voice.sh` | all of the above **plus** STT/TTS — talk to the graph while stepping it |

Never run two modes at once — both drive `/cmd_vel` and bind micro-ROS UDP 8888.
Stop production first: `sudo systemctl stop langrobo-brain langrobo-microros`.

### Voice in dev mode (Studio)

`langgraph dev` serves the graph over HTTP and has no ROS side, so dev mode
loses both the mic and the speaker — agent_node owns the input queue and the
reply sink, not the graph. `studio_voice_node` is that missing pair, and
nothing else: no graph, no bridge, no history.

```bash
./scripts/dev_voice.sh          # micro-ROS + langgraph dev + STT/TTS + bridge
```

It reuses a `langgraph dev` or micro-ROS agent that is already running rather
than fighting it for the port, refuses to start while `langrobo-brain` is up
(both own `/cmd_vel` and `/voice/user_input`), and Ctrl+C stops only what it
started. `--no-voice` reduces it to `dev.sh`; `--no-micro` skips the ESP32 link;
a bare number sets the micro-ROS UDP port. Background logs land in
`/tmp/langrobo-dev/` (override with `LANGROBO_DEV_LOGS`).

The two halves can still be run separately — `./scripts/dev.sh` in one terminal
and `ros2 launch langrobo_ros studio_voice_launch.py` in another:

```bash
./scripts/dev.sh                                   # terminal 1 — graph on :2024
ros2 launch langrobo_ros studio_voice_launch.py    # terminal 2 — voice pair + bridge
```

| Argument | Default | Use |
|---|---|---|
| `voice:=false` | `true` | the pi5 STT/TTS pair is already running |
| `watch_ui:=false` | `true` | stop speaking turns typed into the Studio box |
| `thread_id:=…` | new thread | pin to an existing conversation (id from the Studio URL) so typed and spoken turns share one history |
| `thread_mode:=per_turn` | `persistent` | fresh conversation per utterance (A/B one prompt) |
| `stream_speech:=false` | `true` | speak the whole reply at the end instead of streaming |
| `studio_url:=…` | `http://127.0.0.1:2024` | non-default server |
| `assistant_id:=…` | `agent` | the graph key in `langgraph.json` |

Both directions reach the speaker:

| You | Appears in Studio | Spoken |
|---|---|---|
| speak into the mic | ✅ the bridge's thread is a normal server thread | ✅ streamed sentence by sentence |
| type in the Studio browser box | ✅ (it is the UI's own run) | ✅ once the run finishes |

The asymmetry is the server's, not ours: `join_stream` does **not** replay a
run's token stream to a late joiner (only `values` events arrive — measured
against langgraph-sdk 0.4.2), so a watched turn is spoken from its final state
snapshot rather than token by token. A watcher polls busy threads every
`watch_poll_s`; a run that starts and finishes inside one interval is missed by
design, and runs already in flight when the bridge starts are skipped — after a
restart they belong to the previous session, not to anyone in the room. One speech lock keeps the two paths from interleaving mid-sentence.

A spoken turn is never also spoken as a watched one, but note *how*: run ids
are only known once a run's metadata event arrives, and the poll can beat it
(seen live 2026-09-05 — the watcher joined a mic turn already in flight). So
the guard is the THREAD, not the id: while the bridge is driving a turn, and
for 3s after, every run on its own thread is its own by construction. A pinned
thread is exempt outside that window, so a shared conversation still speaks
turns typed in the UI.

Four more knobs are node parameters rather than launch arguments — reach them
with `ros2 run langrobo_ros studio_voice_node --ros-args -p <name>:=<value>`:
`watch_poll_s` (0.5s), `speak_errors` (speak a short line when the server is
down), `skip_nodes` (agents whose tokens never reach the speaker — default
`supervisor`, which emits only grammar-forced handovers), and `turn_timeout_s`.

Kept from production: the `/voice/*` wire protocol (sentence chunks closed by
`<|eou|>`, via the shared `SentenceEmitter` — so tts_node cannot tell the two
brains apart), replace-newest input queueing, barge-in (a newer utterance
cancels the server run), and the sticky-agent rule (`supervisor` never
persists).

Dropped on purpose — these live in agent_node, and a second implementation
would be a second thing to keep in sync:

| Not in dev mode | Why |
|---|---|
| `[SYSTEM]` turns (reminders, watch, briefing, nav-done) | agent_node's ROS timers |
| Telegram channel | agent_node's poller and reply sink |
| fast path | the zero-LLM movement lane; dev mode exists to watch the graph |
| history trimming | the server thread holds the conversation (checkpointer) |

Movement still works: `graph_studio.py` attaches a real `ROS2Bridge` when ROS2
is sourced, so tools publish `/cmd_vel` from **that** process.
`studio_voice_node` only reads `/voice/user_input` and writes
`/voice/robot_speech` — two processes, no overlap.

Server down or a dropped turn → the node speaks one short offline line and
keeps listening (hard rule 4); the real diagnosis is in the `langgraph dev`
terminal. The client is transport-injected, so
`src/langrobo_core/tests/test_studio.py` covers it with no server running.

### systemd units

```
langrobo-microros.service   micro-ROS agent (ESP32 bridge), Restart=always
langrobo-brain.service      agent_node via scripts/run_brain.sh, Restart=always
```

The brain unit has a drop-in `/etc/systemd/system/langrobo-brain.service.d/10-avahi-ordering.conf`
(repo copy: `src/langrobo_ros/systemd/langrobo-brain.service.d/`) adding
`After=/Wants=avahi-daemon.service` — without it the brain starts before mDNS
is ready and the first slot probe to `singireddys-mac-mini.local` fails with
"Name or service not known" (harmless but noisy; added 2026-07-19).

```bash
systemctl status langrobo-brain langrobo-microros
journalctl -u langrobo-brain -f -o cat            # follow structured JSON logs
journalctl -u langrobo-brain -o cat | jq 'select(.level=="ERROR")'
journalctl -u langrobo-brain -o cat | jq 'select(.trace_id=="<id>")'   # one turn end-to-end
sudo systemctl restart langrobo-brain             # brain only; micro-ROS untouched
```

After editing a unit file in `src/langrobo_ros/systemd/`, the INSTALLED copy
must be refreshed too (bitten 2026-07-10 — the repo file was updated, the
installed one wasn't, and `fleet.sh sim` silently couldn't switch bodies):

```bash
sudo cp src/langrobo_ros/systemd/langrobo-brain.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart langrobo-brain
```

### Robot body switch (rover vs sim)

The brain drives cmd_vel to ONE body at a time, chosen by the `robot_body`
ROS param (default `rover`): the real ESP32 rover gets plain `Twist` on
`/cmd_vel`; the Gazebo sim (`rover_sim`) gets `TwistStamped` on
`/mecanum_drive_controller/cmd_vel`. The switch is plumbed
`fleet.sh {sim|rover}` → `~/.langrobo/brain.env` (`ROBOT_BODY=…`) →
systemd `EnvironmentFile` → `run_brain.sh` → launch arg → param, and
`fleet.sh` restarts the brain only when the body actually changes.
Verify: `curl -s localhost:8090/status | jq .runtime.robot_body`.

### Adaptive KV-slot map

The slot map in `agent_params.yaml` assumes a 5-slot llama.cpp server
(`--parallel 5`). At startup the brain probes the server's real slot count
(`GET /props`) and folds out-of-range pins onto shared slots, least-frequent
agents first (navigate → specialist slot → chat's slot), keeping chat,
local_agent and supervisor on private slots for as long as the server allows.
A 4-slot server just means navigate shares slot 2 again. Probe unreachable →
the configured map is kept. Boot log line: "LLM server reports N parallel
slots" (or per-agent fold warnings).

## Health API

In-process FastAPI on port **8090** (`LANGROBO_HEALTH_PORT`).

```bash
curl -s localhost:8090/health                                          # liveness (no auth)
curl -s -H "Authorization: Bearer $LANGROBO_API_TOKEN" localhost:8090/status | jq
curl -s -H "Authorization: Bearer $LANGROBO_API_TOKEN" localhost:8090/metrics  # Prometheus text
```

`/status` reports: LLM primary health + fallback state, episodic-memory state
and pending writes, sticky agent, last-turn timestamp/duration, camera frame
age, queued system events. Without `LANGROBO_API_TOKEN` the API binds
localhost-only; a LAN bind without a token is refused at startup (fail-fast).

## .env reference

`agent_node` loads `~/ros2_ws/.env` itself (`LANGROBO_ENV_FILE` overrides).
LLM provider/model/slots live in `src/langrobo_ros/config/agent_params.yaml`,
not here.

| Variable | Purpose |
|---|---|
| `LANGROBO_API_TOKEN` | Bearer token for /status + /metrics (empty → localhost only) |
| `LANGROBO_HEALTH_PORT` / `_HOST` | Health API bind (default 8090) |
| `LANGROBO_FALLBACK_PROVIDER/MODEL/API_KEY_ENV/BASE_URL` | Cloud LLM fallback — arms when key exists |
| `LANGROBO_MEMORY` | `false` disables episodic memory |
| `LANGROBO_MEMORY_PATH` | Embedded Qdrant path (default `~/.langrobo/qdrant`) |
| `QDRANT_URL` + `QDRANT_API_KEY` | Switch memory to a Qdrant server / Cloud |
| `LANGROBO_TRACING` | LangSmith opt-in; without it tracing vars are scrubbed (kills stale-key 403 spam) |
| `LANGROBO_LOG_JSON` | `false` → human-readable log lines |
| `OPENAI/ANTHROPIC/GOOGLE_API_KEY` | Cloud provider keys (referenced by name) |
| `SWIGGY_ACCESS_TOKEN` | Legacy Swiggy token override — prefer `scripts/swiggy_login.py` → `~/.langrobo/mcp_tokens.json` (no token anywhere → food/instamart/dineout off, no spam) |
| `LANGROBO_MCP_TOKENS` | MCP token file path override (default `~/.langrobo/mcp_tokens.json`) |
| `SWIGGY_FOOD_MCP_URL` / `SWIGGY_INSTAMART_MCP_URL` / `SWIGGY_DINEOUT_MCP_URL` | MCP endpoint overrides (default `https://mcp.swiggy.com/{food,im,dineout}`) |
| `TAVILY_API_KEY` | Web search in chat (absent → feature off) |
| `LANGROBO_TELEGRAM_TOKEN` | Bot token from @BotFather (absent → channel off) |
| `LANGROBO_TELEGRAM_ALLOWLIST` | `chat_id:Name:role,…` — roles `owner`/`family`/`guest`; channel stays off while empty (bot never talks to strangers) |
| `LANGROBO_QUIET_HOURS` | `HH:MM-HH:MM` — proactive pings queue in this window; replies always send (watch alerts bypass it) |
| `LANGROBO_WATCH` | `false` disables home watch mode entirely |
| `LANGROBO_WATCH_COOLDOWN_S` | Min seconds between watch alerts (default 60) |
| `LANGROBO_WATCH_MIN_CONF` | Person-detection confidence floor (default 0.5) |
| `LANGROBO_CONSOLIDATION` | `false` disables nightly memory consolidation |
| `LANGROBO_CONSOLIDATION_HOUR` | Local hour the nightly run becomes eligible (default 3) |
| `LANGROBO_BRIEFING_HOUR` | Set (e.g. `8`) to enable the daily spoken morning briefing — unset = off |
| `STUDIO_PROVIDER/MODEL/BASE_URL/MAX_TOKENS` | `langgraph dev` only |

Robot state files: `~/.langrobo/` — `household.json`, `reminders.json`,
`errands.json`, `qdrant/`, `telegram_offset`, `telegram_deferred.json`,
`watch.json` (armed state), `consolidation.json` (nightly-run cursor),
`briefing.json` (last briefing day), `mcp_tokens.json` (Swiggy MCP login, 0600).
Back this directory up; delete a file to reset that memory.

## Swiggy login / re-login

Swiggy's MCP servers (food, instamart, dineout) use OAuth 2.1 PKCE: phone +
OTP in a browser, access token good for ~5 days, no refresh flow. Login:

```bash
python3 scripts/swiggy_login.py --verify        # desktop with a browser
# headless Pi5: from your laptop first `ssh -L 8976:localhost:8976 <robot>`,
# then on the Pi:
python3 scripts/swiggy_login.py --no-browser    # open the printed URL on the laptop
```

`--verify` lists the tool counts on all three MCP servers with the fresh
token. The token lands in `~/.langrobo/mcp_tokens.json`; a running brain picks
it up automatically on the next Swiggy/Instamart/Dineout/tracker turn (header
hot-reload) — only a brain that NEVER had a token needs one restart. When the
token expires mid-flight the three agents degrade to a spoken "temporarily
unavailable" and the owners get exactly one Telegram nudge to re-run the
script. Check `curl -s localhost:8090/status | jq .mcp` for per-provider tool
counts and token days-left.

## Household knowledge base (documents)

Send the robot a `.pdf`/`.txt`/`.md` on Telegram (owner/family) — it chunks,
embeds and stores it locally ("Learned 'manual.pdf' — 12 sections"), then the
**knowledge agent** answers questions from it ("what does error E4 mean?").
Re-sending a file replaces its old version. Bulk ingest:
`python3 scripts/ingest_docs.py <files|dir>` — but the embedded Qdrant is
single-process, so stop the brain first (the script detects this and says so).
"what documents do you have?" lists them.

## Morning briefing

Opt-in: set `LANGROBO_BRIEFING_HOUR=8` in `.env`. Once a day at/after that
hour the robot speaks a short summary (today's reminders, weather if Tavily
is configured, list highlights). On demand any time: "give me my briefing".
State: `jq .runtime.briefing` on `/status`.

## Home watch mode

Arm by voice ("Rakhi, watch the house") or Telegram ("watch the house");
disarm with "stop watching" / "I'm back". While armed the Jetson target
finder hunts `person`; a confident detection sends a photo to every
**owner-role** Telegram member (cooldown between alerts) and the robot
announces it aloud. The photo send is deterministic — it works even when the
LLM is down. Armed state survives restarts. Only owner/family may arm or
disarm (guests must not switch the alarm off). Check `curl -s
localhost:8090/status | jq .runtime.watch`.

## Memory consolidation (self-learning)

Once a day at/after `LANGROBO_CONSOLIDATION_HOUR`, while the robot is idle
and the Mac Mini is reachable, new episodic turns are distilled into short
household facts (`facts` Qdrant collection) by the LOCAL model — never the
cloud. `recall_memory` surfaces them alongside episodes. The run aborts the
moment real input arrives and resumes later; re-processing is harmless
(facts deduplicate by embedding similarity). Check `curl -s
localhost:8090/status | jq .runtime.consolidation`.

## Telegram channel

Full setup + usage guide: **TELEGRAM.md**. Short version: create a bot with
**@BotFather**, get each member's chat_id from **@userinfobot**, fill the two
`.env` keys, restart the brain. Each member must message the bot once
(Telegram forbids bots from initiating chats).
Role capabilities live in `langrobo_core/services/permissions.py`
(owner = everything; family = chat/relay/remind — no camera, no driving,
no orders; voice turns act as owner until speaker ID exists).

Behavior: long-polls `getUpdates` (works behind NAT, no public IP); the
update offset persists in `~/.langrobo/telegram_offset` so restarts neither
replay nor drop messages — texts sent while the robot was off are answered at
boot. Inbound is rate-limited to 10 msg/min per sender. `/status` shows the
`telegram` block (`polling`, `last_poll_age_s`, `last_error`) and
`queued_telegram_messages`. Privileged sends are auditable:
`journalctl -u langrobo-brain -o cat | grep "AUDIT telegram"`.

## Deploy checklist (Pi5 + Jetson protocol change)

1. Pi5: `colcon build --symlink-install` + `pip3 install --break-system-packages -r requirements.txt`
2. Jetson (`speech_vision` repo): rebuild `voice_pkg` — **both together when the
   speech protocol changes** (chunks + `<|eou|>` must match tts_node).
3. Mac Mini llama.cpp up: `curl http://singireddys-mac-mini.local:8080/v1/models`
   — must report `"multimodal"` in capabilities (mmproj loaded) for look().
4. `sudo systemctl restart langrobo-brain` → `curl localhost:8090/health`
5. Latency: `python3 scripts/latency_replay.py` — first-audio budget ≤2s warm.
6. Clocks: Pi5↔Jetson must be chrony-peered (replay flags negative deltas otherwise).

## Mac Mini LLM server

```bash
./llama-server -m <model>.gguf --mmproj <mmproj>.gguf --port 8080 -ngl 99 \
               --parallel 4 --jinja
```

- `--parallel 4` — all four slots are pinned by the brain (0 chat / 1 vision /
  2 specialists+consolidation / 3 supervisor — see agent_params.yaml slot map).
- `--jinja` — required for grammar-forced handover + streamed tool calls.
- Prompt cache + context checkpoints give cross-restart KV reuse; slot pinning
  is insurance on top.
- **Reach it by mDNS name only, never a pinned IP** — DHCP moved the Mac
  (.7 → .3, observed 2026-07-19); `singireddys-mac-mini.local` kept resolving.
- **Outage signature** (2026-07-19, 16:26–17:28): every LLM call fails with
  `APIConnectionError`, journal shows "Primary LLM marked down for 60s" each
  consolidation cycle, while the network itself is fine. Cause = the Mac asleep
  or llama-server not running. Brain self-recovers when the server returns —
  no restart needed. Durable fix pending on the Mac: `sudo pmset -a sleep 0`
  + run llama-server as a LaunchAgent.

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| Spoken "my brain server is offline" | Mac Mini down/unreachable → check server, or arm `LANGROBO_FALLBACK_*` |
| Every turn slow (~20s before speech) | KV cache cold: slot scatter (server without `--parallel`/pins), clock in a prompt, or mid-history mutation — see ARCHITECTURE.md KV-cache discipline |
| "I cannot see right now" | Jetson camera node down or frame >10s stale — check `/camera/color/image_raw/compressed`. On the orin-nav stack that topic is a 2 Hz republish from `detections_3d` — it goes dark whenever YOLO is paused (nav safety procedure) or the `vision` layer isn't up |
| Vision turn slow (~60s end-to-end) | Measured 2026-07-19: router call ~43s + vision call ~16s on the Mac, sequential. Known cost of graph routing — text-only nav turns already bypass it via fastpath; a vision fastpath is the open optimization |
| Tool calls flaky / early stops | GGUF chat template mislabels control tokens → suspect the quant; try `strict_tool_calls:=false` |
| Food/grocery/dineout "temporarily unavailable" | No Swiggy token or it expired (~5 days) — run `scripts/swiggy_login.py` (see "Swiggy login" above); `/status .mcp` shows which provider is down |
| Memory unavailable in /status | first boot downloads the embed model (~130MB) — check network, see journal |
| ESP32 not moving | `langrobo-microros` unit down, or ESP32 not on WiFi → `systemctl status langrobo-microros`, then power-cycle ESP32 |
| DDS discovery fails Pi5↔Jetson | `ROS_DOMAIN_ID` mismatch, or a stray `ROS_DISCOVERY_SERVER` in the environment. **Prod is plain multicast since 2026-07-16** (the D555 is a raw DDS participant that discovery-server clients cannot see) — every prod script unsets `ROS_DISCOVERY_SERVER`; `langrobo-discovery` remains only for `dev.sh`/`langgraph dev` (127.0.0.1:11811). A client accidentally pointed at it goes silently invisible to the Jetson |

## Pi5 system record

This machine (`rakhi24-desktop`) is headless (SSH only). The desktop GUI and
unused daemons (cups, bluetooth, ModemManager, GUI stack) were disabled
2026-07-02 to free RAM/CPU. Do **not** disable: `NetworkManager`,
`wpa_supplicant`, `ssh`, `avahi-daemon` (mDNS hostname), `pipewire*` (audio),
`dbus`/`polkit`. Reverse any change with `sudo systemctl enable --now <unit>`.

## Known trade-offs (accepted)

- Wake-word attention window (15s) can answer room chatter right after the
  robot speaks — real fix is gaze attention (P2 face recognition).
- Stop-spotter can miss a soft "stop" during the robot's own speech — real fix
  is AEC hardware (P1 mic array).
- `navigate_to_visible_object` has no obstacle avoidance (mono cam) — drives
  straight at the target; depth camera phase fixes this.
