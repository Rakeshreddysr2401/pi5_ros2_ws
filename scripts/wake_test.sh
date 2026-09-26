#!/usr/bin/env bash
# Live wake-score bar — say the word and watch it. Short to type, so a
# wrapped paste can't split a long path (the same reason run_stt.sh exists).
#
#   ./scripts/wake_test.sh            # the model the robot is using now
#   ./scripts/wake_test.sh rakhi      # test the Rakhi model
#   ./scripts/wake_test.sh mitra 0.5  # ...with a different threshold
#
# Ctrl-C to stop. The robot keeps listening at the same time, so a real
# call also wakes it; `systemctl --user stop langrobo-voice` first if you
# want scores only.

set -uo pipefail
cd "$(dirname "$0")/.."
PARAMS=src/pi5_voice_pkg/config/voice_params.yaml

WORD="${1:-}"
if [ -z "$WORD" ]; then   # default: whatever the robot is configured with
    WORD=$(grep -E '^\s*wake_word:' "$PARAMS" | head -1 | awk '{print $2}')
fi
THRESH="${2:-$(grep -E '^\s*wake_threshold:' "$PARAMS" | head -1 | awk '{print $2}')}"
MODEL="src/langrobo_ros/models/wake/${WORD}.onnx"

if [ ! -f "$MODEL" ]; then
    echo "no model for '$WORD'. Available:"
    ls src/langrobo_ros/models/wake/*.onnx 2>/dev/null | xargs -n1 basename | sed 's/\.onnx$/  /' | sed 's/^/  /'
    exit 1
fi

echo "model: $MODEL   threshold: $THRESH   — say \"$WORD\" (Ctrl-C to stop)"
exec python3 scripts/wake_live_score.py "$MODEL" --threshold "$THRESH"
