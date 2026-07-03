# TODO — pending on-device work

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
