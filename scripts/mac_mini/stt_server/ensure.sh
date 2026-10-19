#!/bin/bash
# Make sure kino-stt (Apple speech for the Pi5, :8091) is up. Never fails --
# llama-server must start either way. Called by the `e12b4` alias before
# llama-server, so the usual "start the Mac" step brings speech up too.
cd "$(dirname "$0")"
up() { curl -s -m 2 localhost:8091/health | grep -q '"authorized"'; }
if up; then echo "kino-stt: speech already listening on :8091"; exit 0; fi
if launchctl print "gui/$(id -u)/com.kino.stt" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/com.kino.stt"        # installed: restart the LaunchAgent
elif [ -d kino-stt.app ]; then
    open kino-stt.app --args 8091                             # built but not installed
else
    echo "kino-stt: not built -- run $(pwd)/install.sh once"; exit 0
fi
for _ in 1 2 3 4 5 6 7 8 9 10; do up && { echo "kino-stt: speech listening on :8091"; exit 0; }; sleep 1; done
echo "kino-stt: NOT answering (log: /tmp/kino-stt.log; permission? System Settings > Privacy > Speech Recognition)"
