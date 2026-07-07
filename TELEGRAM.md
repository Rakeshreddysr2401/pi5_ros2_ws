# Telegram Channel — Setup & Usage Guide

Chat with Rakhi from anywhere: text or photos in, text or photos back, from
the same brain that answers voice in the living room. This is the household
setup guide; internals live in ARCHITECTURE.md ("Telegram channel"), the turn
walkthrough in HOW_IT_WORKS.md §4b, and the env reference in OPERATIONS.md.

Your phone number is never involved — it only logs you into the Telegram app.
The robot connects as a **bot** with its own token.

## Setup (once, ~5 minutes)


1. **Create the bot**: in Telegram, chat with **@BotFather** → `/newbot` →
   pick a display name (`Rakhi`) and a unique username ending in `bot`
   (`RakhiHomeBot`). BotFather returns a **token** (`7123456789:AAHfQx…`).
   Treat it like a password — anyone holding it can impersonate the bot.
2. **Get each member's chat_id**: everyone messages **@userinfobot** once and
   notes the `Id` it replies with.
3. **Unlock the chat**: everyone opens the bot's chat and sends it one
   message ("hi"). Telegram forbids bots from initiating conversations —
   one message unlocks replies forever. Skip this and the robot's sends fail.
4. **Configure** `.env` on the Pi5 (`~/ros2_ws/.env`):

   ```bash
   LANGROBO_TELEGRAM_TOKEN=7123456789:AAHfQx...
   LANGROBO_TELEGRAM_ALLOWLIST=1234567890:Rakesh:owner,9876543210:Mom:family
   # optional — hold proactive pings overnight:
   LANGROBO_QUIET_HOURS=23:00-07:00
   ```

5. **Restart**: `colcon build --symlink-install && sudo systemctl restart langrobo-brain`,
   then check `curl -s localhost:8090/status | jq .telegram` → `"polling": true`.

The channel stays completely off until BOTH token and allowlist are set.
Anyone not on the allowlist is ignored silently — the bot never talks to
strangers, even though its username is publicly searchable.

## Roles — who may do what

| Capability | owner | family | guest |
|---|---|---|---|
| Chat, questions, reminders | ✅ | ✅ | ✅ (chat only) |
| Relay messages ("tell Rakesh…") | ✅ | ✅ | ❌ |
| Announce aloud in the house | ✅ | ✅ | ❌ |
| Arm/disarm home watch | ✅ | ✅ | ❌ |
| Add documents to the knowledge base | ✅ | ✅ | ❌ |
| Request camera photos | ✅ | ❌ | ❌ |
| Move the robot | ✅ | ❌ | ❌ |
| Place food orders | ✅ | ❌ | ❌ |

Checks are enforced inside the tools (`langrobo_core/services/permissions.py`),
not just prompts — a refusal offers to ask the owner instead. Voice has no
speaker identity yet, so spoken commands act as owner. Every privileged send
is logged: `journalctl -u langrobo-brain -o cat | grep "AUDIT telegram"`.

## What you can do

- **Chat from anywhere** — "is anyone home?", "what's on the shopping list?"
  Same memory and household knowledge as voice.
- **Get a photo** — "send me a pic of the room" (owner only; taken from where
  the robot currently stands — room-to-room arrives with phase-2 navigation).
- **Send a photo** — attach a picture (+ optional caption question); the
  multimodal model actually sees it.
- **Relay by voice** — say "Rakhi, tell Mom I'll be late" → lands on Mom's
  phone as a Telegram message.
- **Speak into the house** — text "announce that dinner is ready" and the
  robot says it out loud at home, then confirms to you. A bare "tell Mom X"
  gets one question back first (her phone, or aloud?). During quiet hours it
  refuses and offers alternatives — announces anyway only if you insist.
- **Watch the house** — text "watch the house" / "stop watching" (see
  OPERATIONS.md, Home watch mode). Person seen while armed → photo alert.
- **Teach it documents** — send a `.pdf`/`.txt`/`.md` file (appliance manual,
  notes) → "Learned 'manual.pdf' — 12 sections". Then ask about it from
  anywhere ("what does error E4 mean on the washer?"). Attach a caption
  question and it answers right after learning. Re-send a file to update it.
- **Middleman** — "ask Mom when she's back **and let me know**": her eventual
  reply is routed back — spoken aloud if you asked aloud, texted if you
  texted. Open errands live in `~/.langrobo/errands.json` and expire after 24h.
- **Proactive pings** — reminders and delivery events can reach your phone;
  during quiet hours they queue and send in the morning (direct replies are
  never held).

## Limits & troubleshooting

- Texts are answered one at a time behind voice turns — the person in the
  room always wins. Flooding is capped at 10 messages/min per sender.
- Messages sent while the robot was off are answered at boot (the getUpdates
  offset persists in `~/.langrobo/telegram_offset`).
- Voice notes, stickers, documents → "I can only read text and photos for now."
- Bot never replies? Check allowlist chat_id, confirm you messaged the bot
  once, then `jq .telegram` on `/status` — `last_error` says what's wrong.
- Replies say "Telegram isn't set up"? Token or allowlist missing/malformed —
  the startup log's `Settings:` line shows `telegram=off` vs member count.
- Only ONE poller may run per token: if you also start the brain elsewhere
  (e.g. a second Pi or a tinkering session), the two steal each other's
  updates. Studio (`langgraph dev`) is safe — it sends but never polls.
