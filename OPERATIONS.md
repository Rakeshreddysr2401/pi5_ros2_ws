
# LangRobo — Operations

Deploy, run, observe, and troubleshoot the Pi5 brain.

---

## Run modes

| Mode | Command | Use |
|---|---|---|
| **Production** | `./scripts/install_systemd.sh` (once) | 24/7: auto-restart, boot persistence, JSON logs in journald |
| Foreground | `ros2 launch langrobo_ros brain_launch.py` | attended testing (micro-ROS included) |
| Dev / Studio | `./scripts/dev.sh` | LangGraph Studio UI + micro-ROS agent |

Never run two modes at once — both drive `/cmd_vel` and bind micro-ROS UDP 8888.
Stop production first: `sudo systemctl stop langrobo-brain langrobo-microros`.

### systemd units

```
langrobo-microros.service   micro-ROS agent (ESP32 bridge), Restart=always
langrobo-brain.service      agent_node via scripts/run_brain.sh, Restart=always
```

```bash
systemctl status langrobo-brain langrobo-microros
journalctl -u langrobo-brain -f -o cat            # follow structured JSON logs
journalctl -u langrobo-brain -o cat | jq 'select(.level=="ERROR")'
journalctl -u langrobo-brain -o cat | jq 'select(.trace_id=="<id>")'   # one turn end-to-end
sudo systemctl restart langrobo-brain             # brain only; micro-ROS untouched
```

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
| `SWIGGY_ACCESS_TOKEN` | Food ordering (absent → feature off, no spam) |
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
`briefing.json` (last briefing day).
Back this directory up; delete a file to reset that memory.

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

- `--parallel 4` — slots 0/1/2 are pinned by the brain (chat / vision / specialists).
- `--jinja` — required for grammar-forced handover + streamed tool calls.
- Prompt cache + context checkpoints give cross-restart KV reuse; slot pinning
  is insurance on top.

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| Spoken "my brain server is offline" | Mac Mini down/unreachable → check server, or arm `LANGROBO_FALLBACK_*` |
| Every turn slow (~20s before speech) | KV cache cold: slot scatter (server without `--parallel`/pins), clock in a prompt, or mid-history mutation — see ARCHITECTURE.md KV-cache discipline |
| "I cannot see right now" | Jetson camera node down or frame >10s stale — check `/camera/color/image_raw/compressed` |
| Tool calls flaky / early stops | GGUF chat template mislabels control tokens → suspect the quant; try `strict_tool_calls:=false` |
| Food ordering "not configured" | `SWIGGY_ACCESS_TOKEN` absent/401 — feature is off by design until a valid token lands |
| Memory unavailable in /status | first boot downloads the embed model (~130MB) — check network, see journal |
| ESP32 not moving | `langrobo-microros` unit down, or ESP32 not on WiFi → `systemctl status langrobo-microros`, then power-cycle ESP32 |
| DDS discovery fails Pi5↔Jetson | `ROS_DOMAIN_ID` mismatch, `langrobo-discovery` (meeting point) down, or a client started before the meeting point came up — see NETWORKING.md; restart the client (brain/microros/Jetson launch) after the meeting point is confirmed up |

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
