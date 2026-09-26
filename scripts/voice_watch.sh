#!/usr/bin/env bash
# Watch the voice pipeline live — say "Mitra" and see what happens.
#
#   ./scripts/voice_watch.sh          # voice only (wake word, what it heard)
#   ./scripts/voice_watch.sh all      # + the brain (which agent, the reply)
#
# Ctrl-C to stop. What the lines mean:
#   asleep peak_wake_score=0.02   idle noise floor (heartbeat every 10 s)
#   wake word ... detected        it heard "Mitra" and is now listening
#   command: "..."                what it understood and sent to the brain
#   follow-up window closed       nobody spoke in time; back to sleep
#   audio ready / not ready       speaker+mic appeared / went away

set -uo pipefail
FILTER='wake word|peak_wake_score|command:|follow-up|addressed|not addressed|audio (ready|not ready)|stop word|dropped|too old|hit the'

if [ "${1:-voice}" = "all" ]; then
    # Voice runs as a user unit, the brain as a system unit — one stream each.
    journalctl --user -u langrobo-voice -f -o cat &
    VOICE_PID=$!
    trap 'kill $VOICE_PID 2>/dev/null' EXIT
    journalctl -u langrobo-brain -f -o cat | grep --line-buffered -E 'Invoking graph|→ TTS|→ Telegram|Graph error|empty response'
else
    journalctl --user -u langrobo-voice -f -o cat | grep --line-buffered -E "$FILTER"
fi
