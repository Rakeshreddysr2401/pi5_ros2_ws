
# LangRobo — Operations

Deploy, run, observe, and troubleshoot the Pi5 brain.

---

## Run modes

| mode | command | what runs |
|---|---|---|
| Production | `systemctl start langrobo-brain langrobo-microros` | the brain + micro-ROS agent, 24/7, auto-restart, JSON logs |
| Foreground | `ros2 launch langrobo_ros brain_launch.py` | the same, in your terminal |
| Dev (Studio) | `./scripts/dev.sh` | micro-ROS + `langgraph dev` on :2024 — **draws the graph**, and lets you step a turn node by node |
| Voice | `ros2 launch pi5_voice_pkg voice_launch.py` | CPU-only STT + TTS on the Pi 5 (see PI5_VOICE.md) |

**Never run two brains at once** — both drive `/cmd_vel` and micro-ROS UDP 8888.

The llama.cpp server on the Mac Mini must be started with **`--jinja --parallel 3`**:
one KV-cache slot per agent (chat, local_agent, navigate). With fewer slots the
agents share and evict each other's cached prompt prefix, which
costs ~18-50s of re-prefill per turn. agent_node probes the server at boot and
logs `KV slot map (one per agent): {...}` — or a warning naming the shortfall.
See ARCHITECTURE_LLD.md §4.1.

## Health API

In-process FastAPI on port **8090** (`LANGROBO_HEALTH_PORT`).

```bash
curl -s localhost:8090/health                                          # liveness (no auth)
curl -s -H "Authorization: Bearer $LANGROBO_API_TOKEN" localhost:8090/status | jq
curl -s -H "Authorization: Bearer $LANGROBO_API_TOKEN" localhost:8090/metrics  # Prometheus text
```

`/status` reports: LLM primary health + fallback state, Telegram channel
state, sticky agent, last-turn timestamp/duration, camera frame age, robot
body, queued system/Telegram events. Without `LANGROBO_API_TOKEN` the API binds
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
| `LANGROBO_TRACING` | LangSmith opt-in; without it tracing vars are scrubbed (kills stale-key 403 spam) |
| `LANGROBO_LOG_JSON` | `false` → human-readable log lines |
| `OPENAI/ANTHROPIC/GOOGLE_API_KEY` | Cloud provider keys (referenced by name) |
| `TAVILY_API_KEY` | Web search in chat (absent → feature off) |
| `LANGROBO_TELEGRAM_TOKEN` | Bot token from @BotFather (absent → channel off) |
| `LANGROBO_TELEGRAM_ALLOWLIST` | `chat_id:Name:role,…` — roles `owner`/`family`/`guest`; channel stays off while empty (bot never talks to strangers) |
| `LANGROBO_QUIET_HOURS` | `HH:MM-HH:MM` — proactive pings queue in this window; replies to a person always send |
| `STUDIO_PROVIDER/MODEL/BASE_URL/MAX_TOKENS` | `langgraph dev` only |
| `LANGROBO_NAV_FRAME` | Frame for goals, TF pose reads and detections (default `odom` — this rover has no map frame) |
| `LANGROBO_STANDOFF_M` | How far short of an object the robot parks (default 0.45). The Jetson's `pixel_to_goal.py` reads the SAME variable — export it on both machines or the two halves disagree |
| `LANGROBO_PAN_TILT` | `1` once pan/tilt servos are actually fitted (default off — the ESP32 has no servo subscriptions) |
| `LANGROBO_LINEAR_VEL_MS` / `_PHYSICAL_VEL_MS` / `_ANGULAR_VEL_RS` / `_STEADY_ANGULAR_VEL` | Drive calibration, tunable without a rebuild — see INTEGRATION_GAPS.md §3 |

Robot state files: `~/.langrobo/` — `locations.json` (spots saved with
`save_location`), `telegram_offset` (exactly-once inbound across restarts),
`telegram_deferred.json` (quiet-hours queue). Back this directory up; delete a
file to reset that memory.

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

## LangSmith tracing

Off by default. Turn it on with **both** `LANGROBO_TRACING=true` and a
`LANGSMITH_API_KEY` in `.env` (either alone → `services/config.sanitize_tracing_env`
scrubs the tracing vars, which is what keeps a stale key from spamming a 403 on
every LLM call). `LANGCHAIN_PROJECT` names the project in the UI. Restart the
brain to pick it up.

What a turn looks like in the UI:

| | |
|---|---|
| Run name | `turn:voice` / `turn:telegram` / `turn:system`, or `fastpath:voice` for the zero-LLM movement lane |
| Tags | `channel:<voice\|telegram\|system>`, `entry:<agent>` |
| Tree | `turn_entry → <agent> → <agent>_tools → …`, one child chat-model run per LLM call, tool runs underneath |
| Metadata | `trace_id`, `channel`, `entry_agent`, `sticky_agent`, `llm_provider`, `llm_base_url`, `llm_primary_available`, `llm_fallback`; Telegram turns add `sender_name`/`sender_role`/`telegram_photo`; voice turns add `stt_*` (provider, `fell_back`, latency, RTF) |
| Sibling runs | `tts:<provider>` — one per synthesised sentence, carrying the same `trace_id` |

`trace_id` is the join key across all three observability surfaces for one turn:
the LangSmith runs, `journalctl -u langrobo-brain -f -o cat` (every JSON log line
carries it), and the `/diag/timing` waterfall (`scripts/latency_replay.py`).

Useful filters (Filters → Metadata in the UI picks key/value from a dropdown;
the raw query equivalents are below):

| Question | Raw filter |
|---|---|
| Only turns that came in over Telegram | `has(tags, "channel:telegram")` |
| Turns the Mac Mini missed, answered by the cloud fallback | `and(eq(metadata_key, "llm_primary_available"), eq(metadata_value, "false"))` |
| Turns where cloud STT failed and local Whisper silently took over (no Telugu→English translation on those) | `and(eq(metadata_key, "stt_fell_back"), eq(metadata_value, "true"))` |
| Every leg of one turn — STT metadata, the LLM run, each spoken sentence | `and(eq(metadata_key, "trace_id"), eq(metadata_value, "<id>"))` |
| Hide the KV-cache prefills (`cache_warm:chat` / `cache_warm:local_agent` — full prompt, no answer) | `-has(tags, "cache_warm")` |

Cost: LangSmith batches uploads on a background thread, so the turn path does not
wait on the network. It does ship conversation content (prompts, replies, camera
images that entered the context) to LangSmith — leave it off unless you are
debugging.

## Deploy checklist (Pi5 + Jetson protocol change)

1. Pi5: `colcon build --symlink-install` + `pip3 install --break-system-packages -r requirements.txt`
2. Mac Mini llama.cpp up, **started with `--jinja --parallel 3`**:
   `curl http://singireddys-mac-mini.local:8080/v1/models` must report
   `"multimodal"` in capabilities (mmproj loaded) for look().
3. `sudo systemctl restart langrobo-brain` → `curl localhost:8090/health`
4. Check the boot log for `KV slot map (one per agent)` — a warning there
   means the server has fewer slots than agents and every turn will pay
   re-prefill. Fix the server, not the brain.
5. Jetson: `./rover nav && ./rover vlm` in the perception repo. Without
   `./rover vlm` there is no camera frame for `look()` and no depth grounding
   for `approach_described_object`.
6. Latency: `python3 scripts/latency_replay.py` — first-audio budget ≤2s warm.
7. Clocks: Pi5↔Jetson must be chrony-peered (replay flags negative deltas otherwise).

## Mac Mini LLM server

```bash
./llama-server -m <model>.gguf --mmproj <mmproj>.gguf --port 8080 -ngl 99 \
               --parallel 3 --jinja
```

- `--parallel 3` — one KV slot per agent, pinned by the brain: 0 chat,
  1 local_agent, 2 navigate. The map is `registry.SLOTS`, declared
  beside the agents; agent_node probes this server's real slot count at boot
  and warns if it is smaller. See ARCHITECTURE_LLD.md §4.1.
- `--jinja` — required for grammar-forced handover + streamed tool calls.
- Prompt cache + context checkpoints give cross-restart KV reuse; slot pinning
  is insurance on top.
- **Reach it by mDNS name only, never a pinned IP** — DHCP moved the Mac
  (.7 → .3, observed 2026-07-19); `singireddys-mac-mini.local` kept resolving.
- **Outage signature** (2026-07-19, 16:26–17:28): every LLM call fails with
  `APIConnectionError` and the journal shows "Primary LLM marked down for 60s",
  while the network itself is fine. Cause = the Mac asleep
  or llama-server not running. Brain self-recovers when the server returns —
  no restart needed. Durable fix pending on the Mac: `sudo pmset -a sleep 0`
  + run llama-server as a LaunchAgent.

## Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| Spoken "my brain server is offline" | Mac Mini down/unreachable → check server, or arm `LANGROBO_FALLBACK_*` |
| Every turn slow (~20s before speech) | KV cache cold: the server started without `--parallel 3` (agents share slots and evict each other — the boot log says so), a clock in a prompt, or a mid-history mutation. See ARCHITECTURE_LLD.md §4 |
| "I cannot see right now" | Frame >10s stale or absent. That topic is published by `phase4/nodes/image_bridge.py` **in the perception repo** — start it with `./rover vlm` on the Jetson. It also skips encoding entirely when nothing is subscribed, so check the brain is up before blaming the Jetson |
| Vision turn slow (~60s end-to-end) | Measured 2026-07-19: router call ~43s + vision call ~16s on the Mac, sequential. `local_agent` is sticky, so the FOLLOW-UP question about the same scene skips the router; the first one still pays it |
| Tool calls flaky / early stops | GGUF chat template mislabels control tokens → suspect the quant, and check the server has `--jinja` |
| "I couldn't measure its distance" | `pixel_to_goal.py` isn't running on the Jetson (`./rover vlm`), or depth had a hole at that pixel — the reason string says which |
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
- `approach_described_object` costs a VLM round-trip (~10-40s) per look, and
  a search sweep is up to five of them. The cheap alternative — a detector
  streaming object positions the brain can look up in microseconds — needs
  `/vision/detections_3d`, which nothing on the rover publishes yet
  (INTEGRATION_GAPS.md §1).
- The robot sees nothing below 10 cm, above 24 cm, outside 87°, or **downward
  at all** — there is no drop-off detection. Autonomous runs need a human
  watching. (Measured in the rover repo; it is a property of the D555 mount,
  not of this code.)
