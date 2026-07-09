# TODO — pending on-device work

## Deploy the 2026-07-10 reliability fixes (built + tested on the laptop)

Two turn-pipeline fixes, unit-tested off-robot (100 tests green); need the
usual Pi5 rebuild when the robot is next up:

1. **Unknown-agent handover no longer kills the turn silently**
   (`graph/handover_resolver.py`): a hallucinated `next_agent` (possible on
   the cloud fallback — schema enums are advisory there) or a ToolNode
   validation-error payload used to become `Command(goto=<garbage>)`, which
   langgraph silently ignores → turn ended with NO reply. Now rerouted to
   chat with a routing note so the user always gets an answer.
   Covered by `test_unknown_handover_reroutes_to_chat`.
2. **Empty-response turns no longer mutate sticky routing**
   (`agent_node.py _process`): the sticky-agent update now happens only for
   persisted turns — same invariant as barge-in (a discarded turn leaves
   history AND routing state untouched).

On the Pi5: pull, `colcon build --symlink-install && sudo systemctl restart
langrobo-brain`, then one normal voice turn + one reminder cycle as a sanity
check (`latency_replay.py` waterfall unchanged). Delete this section when done.

## Investigate: supervisor never reuses its KV slot on [SYSTEM] turns

Found 2026-07-06 ~04:45 while verifying the 4-slot map. Clean consecutive
reminder cycles, `/slots` `n_prompt_tokens_processed` per slot:
chat=24, local_agent=37, specialists=150 (all reusing) — **supervisor=1791,
a FULL prefill every [SYSTEM] turn** (~15s of the ~50s system-turn cost).
Grammar/tool_choice is NOT the cause (A/B'd directly: identical repeat 5s,
appended tail 5.5s, both reused).
User-facing impact: proactive announcements (reminders/watch alerts) take
~20s+; user turns are unaffected (1.6s warm).

**Update 2026-07-10 (offline investigation from the laptop — robot was down):
could NOT reproduce with current code.** `scripts/kv_replay_supervisor.py`
(new) drives the real graph through two consecutive [SYSTEM] reminder turns
with a production-shaped history and proves BOTH halves behave:

- Client side: consecutive supervisor request bodies are strictly append-only
  (identical body fields; call 2 = call 1's messages + routing note + chat
  reply + new [SYSTEM] msg). Verified with tool-call turns, a camera frame,
  and mid-history routing notes in the history.
- Server side: replaying those payloads against the LIVE Mac Mini on
  id_slot=3 reuses fine — A repeat: prompt_n=32, B: prompt_n=180 (tail only),
  WITH grammar-forced tool_choice and mid-history system-role notes.
- Bonus datum: the cold replay showed `cache_n=589` — production slot 3 held
  a cache sharing exactly the static supervisor prefix (prompt + tool
  schemas), suggesting the production divergence started where HISTORY
  begins, i.e. 1791 ≈ the history portion, not literally byte 0.

Ruled out: registry ordering, projection append-only violations, trim_history,
the Gemma template's mid-history system-role handling, grammar × cache, slot
ctx overflow (n_ctx_slot=86k). The 07-06 observation therefore needs a
production ingredient the sim lacks — or was fixed by a commit since 07-06.

**Next step when the brain is back on the Pi5:**
1. `python3 scripts/kv_replay_supervisor.py --server http://singireddys-mac-mini.local:8080`
   from the Pi5 (sanity: same PASS expected).
2. Trigger two 1-minute reminders ("set a timer for one minute", twice), then
   check the server: two consecutive real [SYSTEM] turns — if `/slots` shows
   supervisor full-prefills again, capture the REAL request bodies via a
   logging proxy on `base_url` and diff against the script's payloads; the
   first differing message is the answer.
3. If it reuses now: bug was fixed in the 07-06..07-10 commits — delete this
   section.

## Deploy the 2026-07-06 feature batch (watch mode + consolidation + prompts.py)

Built and unit-tested on the Pi5 (77 tests green); needs a brain rebuild and
the Jetson back online for full verification. **The Jetson was off/off-network
on 2026-07-06** — the whole voice/vision side is down until it's powered.

1. **Pi5** — ✅ DONE 2026-07-06 01:20: rebuilt + restarted on
   `dev-1.0.9_fable`. Verified live: full voice turn end-to-end (injection →
   chat → get_current_time → streamed TTS), watch mode armed by voice, a REAL
   person-detection alert (photo delivered to Rakesh's Telegram + spoken
   announcement), disarm by voice. Camera frames flow after restart
   (loopback discovery fix holds).
2. **Jetson** — ✅ DONE 2026-07-06: ai_stack up (compose exit-127 was the
   sudo-compose gotcha), voice_pkg/vision_pkg/bringup_pkg rebuilt, stack
   launched, camera frames verified flowing to the Pi5. WiFi-only networking
   workarounds applied — see NETWORKING.md "State of the world 2026-07-06".
3. **Jetson wake alias** — ✅ "chotu/chottu/choto/shotu" added to
   `wake_aliases` (voice_params.yaml) and deployed. CAVEAT: the NEURAL wake
   gate still runs the stock `hey_jarvis_v0.1` openWakeWord model — "hey
   chotu" only works via the transcript fallback path. A custom openWakeWord
   model ("hey chotu" / "hey rakhi") must be trained offline for it to be a
   true wake word (stt_node.py wake_models param is the slot).
4. **Verify watch mode** — ✅ core loop verified live 2026-07-06 (arm by
   voice → real person detection → photo delivered to Telegram → spoken
   announcement → disarm by voice). Still worth spot-checking by hand:
   - stand in view past one alert → NO second alert within the 60s cooldown
   - restart the brain while armed → still armed
   - from Telegram: "watch the house" works; from a guest account: refused
5. **Verify consolidation** (or wait a night):
   - temporarily set `LANGROBO_CONSOLIDATION_HOUR` to the current hour,
     restart, stay idle ≤60s → journalctl shows "Consolidation complete";
     `jq .runtime.consolidation` shows the run; "what do you know about me?"
     → recall surfaces a learned fact. Revert the hour.
6. **Verify follow-me deferral**: "follow me" → robot says person following
   is coming soon (no wheel motion).
7. One normal voice turn + `python3 scripts/latency_replay.py "utterance"`
   → waterfall unchanged.

Delete this section when done.

## Deploy the cross-repo bug-fix batch (2026-07-04 — code done, robots not updated)

**Update 2026-07-06: the Pi5 half is live** (brain restarted after commit
8d4eff9, services healthy). The Jetson half + all voice verification below
are still pending — the Jetson was offline.

Twelve production bugs fixed across BOTH repos (details in the commit messages).
The music contract gained a `cmd_t` field, so deploy the Pi5 and the Jetson
together. When at the devices:

1. **Pi5** — pull branch `dev-1.0.7_with_fable_restructure_speech_imp`, then:
   `colcon build --symlink-install && sudo systemctl restart langrobo-brain`
2. **Jetson** — `ssh rakhi24@192.168.2.20`, in `~/robot` pull branch
   `dev-1.0.4_voice_upgrade_improve`, then rebuild + restart the voice nodes
   inside the `ai_stack` container (procedure in that repo's CLAUDE.md /
   VOICE_PIPELINE.md — colcon build with the venv python, restart the launch).
3. **Verify the fixed flows on-device** (each maps to a fix):
   - Wake barge-in during music: "hey jarvis" while a song plays → TTS stops
     listening-side, **music keeps playing** (previously the Pi5 sweep killed it).
   - Wake barge-in while the robot is saying a sentence containing "stop" →
     barge-in must still work (self-echo guard bypass).
   - Say "stop" mid-reply, then `sudo systemctl restart langrobo-brain` on the
     Pi5, then a fresh turn → the robot's next utterance must NOT be swallowed
     ("I'm ready." should be heard after restart).
   - A turn that ends with no reply must stay silent — the robot must never
     repeat its previous answer.
   - "Play Shape of You" while another song is already playing → the reply
     must confirm the NEW title (cmd_t match, not the old song's heartbeat).
   - Kill music_node mid-song (`docker exec … pkill -f music_node`) → within
     ~5s "what's playing?" says nothing is playing, and the mic still wakes
     normally (no stuck playback gating).
   - "Go near the cup", then cover the camera mid-approach → wheels stop
     within ~1.5s and the robot says it can't see (no blind driving).
   - "Find the bottle" with no bottle in view → the robot scans a full circle
     (~2.4s of rotation) before giving up, not ~40°.
   - One normal voice turn + `python3 scripts/latency_replay.py "utterance"`
     → latency waterfall unchanged.
4. Long-uptime checks (passive): mic still live after days (pw-cat stderr
   drain), no ghost NOW PLAYING in `curl -s localhost:8090/status | jq`.

Delete this section when done.

## Deploy Telegram channel to the Pi5 (code is done, robot not yet updated)

Everything is built, tested, and verified from the laptop (message really
delivered to Rakesh's phone via @RakhiHomeBot). The robot itself hasn't been
updated yet. When at the device:

1. Add to the Pi5's `~/ros2_ws/.env` (values are in the laptop repo's `.env`,
   gitignored — bot token from @BotFather + `1408231700:Rakesh:owner`):
   `LANGROBO_TELEGRAM_TOKEN=…` and `LANGROBO_TELEGRAM_ALLOWLIST=…`
2. Pull this branch on the Pi5, then:
   `colcon build --symlink-install && sudo systemctl restart langrobo-brain`
3. Verify: `curl -s localhost:8090/status | jq .telegram` → `"polling": true`
4. Test sequence (see TELEGRAM.md):
   - text the bot: "hello, is anyone home?"
   - "send me a pic" (photo from the robot camera)
   - send the bot a photo with a question (multimodal in)
   - by voice: "tell Rakesh on Telegram I said hi" + an errand with report-back
   - one voice turn + `python3 scripts/latency_replay.py` — confirm voice
     latency untouched
5. Add Mom later: her chat_id + one "hi" to the bot, append
   `,her_id:Mom:family` to the allowlist, restart.

Also documented in OPERATIONS.md / TELEGRAM.md. Delete this section when done.
