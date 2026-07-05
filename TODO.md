# TODO — pending on-device work

## Deploy the 2026-07-06 feature batch (watch mode + consolidation + prompts.py)

Built and unit-tested on the Pi5 (77 tests green); needs a brain rebuild and
the Jetson back online for full verification. **The Jetson was off/off-network
on 2026-07-06** — the whole voice/vision side is down until it's powered.

1. **Pi5** — on branch `dev-1.0.9_fable`:
   `colcon build --symlink-install && sudo systemctl restart langrobo-brain`
2. **Jetson** — power it on; check it registers with the discovery server
   (`ROS_SUPER_CLIENT=1 ros2 node list` from the Pi5, see NETWORKING.md);
   then pull + rebuild per the 2026-07-04 batch below (still undeployed there).
3. **Jetson wake alias** — add "hey chotu" to the wake gate's `wake_aliases`
   (speech_vision repo, wake_gate.py / its params) while in there.
4. **Verify watch mode**:
   - "Rakhi, watch the house" → confirms armed;
     `curl -s localhost:8090/status | jq .runtime.watch` → `"armed": true`
   - walk into view → phone gets a photo within ~5s, robot announces aloud;
     stand there → NO second alert within the 60s cooldown
   - "stop watching" → disarmed; restart the brain while armed → still armed
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
