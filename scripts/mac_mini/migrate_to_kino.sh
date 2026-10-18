#!/bin/zsh
# migrate_to_kino.sh -- ONE-TIME, on the Mac Mini: move the robot's services to the
# name KINO. Run it once in the Mac's own GUI session, then delete this file.
#
#   1. builds the patched llama-server in ~/llama.cpp-kino (build_llama_kino.sh)
#   2. stops and removes the services installed under the robot's old name
#   3. installs kino-stt (macOS asks ONCE for Speech Recognition permission:
#      the app has a new bundle id, so the old grant does not carry over)
#   4. says which old folders are now safe to delete
#
# This is the only file that knows the old name; nothing else uses it.
set -eu
OLD=mitra                                  # the robot's old name (2026-10-18)
HERE=${0:A:h}
U=$(id -u)

echo "1/4 llama-server in ~/llama.cpp-kino (a fresh build: ~5-10 min)"
"$HERE/build_llama_kino.sh"

echo "2/4 the old services"
launchctl bootout "gui/$U/com.$OLD.llama-server" 2>/dev/null && echo "    stopped com.$OLD.llama-server" || true
launchctl bootout "gui/$U/com.$OLD.stt" 2>/dev/null && echo "    stopped com.$OLD.stt" || true
rm -f ~/Library/LaunchAgents/com.$OLD.stt.plist
pkill -f "$OLD-stt.app/Contents/MacOS/$OLD-stt" 2>/dev/null || true

echo "3/4 kino-stt (click Allow on the Speech Recognition prompt)"
"$HERE/stt_server/install.sh"

echo "4/4 done. Start the LLM as always: e12b5"
echo "    Safe to delete once e12b5 answers: ~/llama.cpp-$OLD and $HERE/stt_server/$OLD-stt.app"
echo "    Then delete this script: git rm scripts/mac_mini/migrate_to_kino.sh"
