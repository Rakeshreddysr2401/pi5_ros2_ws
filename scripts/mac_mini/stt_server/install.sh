#!/bin/bash
# Build mitra-stt.app and keep it running at login (LaunchAgent com.mitra.stt).
#   ./install.sh            build + install + start
#   ./install.sh uninstall  stop + remove the LaunchAgent
# Run in the Mac's own GUI session the first time: macOS asks once for
# Speech Recognition permission (System Settings > Privacy & Security).
set -euo pipefail
cd "$(dirname "$0")"
AGENT=~/Library/LaunchAgents/com.mitra.stt.plist
launchctl bootout "gui/$(id -u)/com.mitra.stt" 2>/dev/null || true
if [ "${1:-}" = uninstall ]; then rm -f "$AGENT"; echo "removed"; exit 0; fi
pkill -f 'mitra-stt.app/Contents/MacOS/mitra-stt' 2>/dev/null || true
./build.sh
# First start through `open`, so the permission prompt belongs to the app.
if ! curl -s -m 2 localhost:8091/health | grep -q '"authorized"'; then
    open mitra-stt.app --args 8091
    echo "click Allow on the Speech Recognition prompt..."
    for _ in $(seq 1 60); do curl -s -m 2 localhost:8091/health | grep -q '"authorized"' && break; sleep 2; done
    pkill -f 'mitra-stt.app/Contents/MacOS/mitra-stt' || true; sleep 1
fi
sed "s|__APP__|$(pwd)/mitra-stt.app|" com.mitra.stt.plist > "$AGENT"
launchctl bootstrap "gui/$(id -u)" "$AGENT"
sleep 2
curl -s localhost:8091/health && echo && echo "mitra-stt running (log: /tmp/mitra-stt.log)"
