#!/usr/bin/env bash
# LangRobo fleet launcher — run on the Pi5. One command to bring up the robot.
# The REAL rover is the default: `./scripts/fleet.sh` with no argument (or `up`,
# `start`) is `rover`. The simulated robot runs only when named (`twin`).
#
#   ./scripts/fleet.sh rover    # REAL body (the default): Pi5 micro-ROS agent (ESP32 wheels)
#                               #   + the Jetson's ~/rover stack, via `./rover up`
#                               #   (D555 + lidar + fused pose + slam + nvblox +
#                               #   nav2 + the VLM bridge + Studio + laptop RViz).
#                               #   Voice is the Pi5's own trio.
#   ./scripts/fleet.sh twin [up|down|status]
#                               # the TWIN (the simulated robot), all on ROS domain 42;
#                               #   the real robot (domain 0) is untouched. The laptop
#                               #   (Isaac Sim, kino_sim: `./sim up home_real` THERE) is
#                               #   ONLY the hardware -- simulated D555, C1, ESP32 + encoders,
#                               #   the measured body -- and the Jetson runs the rover's WHOLE
#                               #   stack on it, `TWIN=1 ./rover up` (cuVSLAM, fusion2,
#                               #   slam_toolbox, nvblox, nav2, reach, VLM bridge), plus a
#                               #   SECOND brain here (twin_brain.sh). The twin announces
#                               #   itself on domain 42 (/sim/hardware): nothing here or on
#                               #   the Jetson logs in to the laptop.
#   ./scripts/fleet.sh stop     # park the robot: stop the body, keep brain up
#   ./scripts/fleet.sh down     # full shutdown incl. this Pi5's services (sudo)
#   ./scripts/fleet.sh status   # who's up, everywhere
#   ./scripts/fleet.sh check    # is it WORKING: every link proved with data, from here
#
# The brain (langrobo-brain) runs here in both modes. Each machine can still
# be started by itself:
#   laptop:  cd ~/kino_sim && ./sim up home_real    (the twin's hardware + world)
#   jetson:  cd ~/rover && ./rover up      (TWIN=1 for the twin; or one layer: ./rover camera, ...)
#
# The Jetson's live repo is ~/rover (container `rover`). The older ~/robot
# roles (`ai_stack` voice, `isaac_ros` perception) are retired: those
# containers no longer exist, so this script no longer calls them.
#
# Machines are reached by mDNS name (NETWORKING.md) with passwordless ssh.

set -eo pipefail
CMD="${1:-rover}"     # no argument = start the REAL rover; sims only when named

# mDNS names by default; override when mDNS flakes (it does, transiently).
JETSON_HOST="${LANGROBO_JETSON_HOST:-rakhi-jetson.local}"
# After a power cycle the Jetson's avahi can lose its name (a clash renames it
# rakhi-jetson-N.local) while the Jetson itself is fine; "unreachable" then sent
# us looking at a healthy machine (2026-10-09). Remember the last address the
# name gave, and fall back to it -- saying so, with the fix.
JETSON_IP_CACHE="${LANGROBO_STATE_DIR:-$HOME/.langrobo}/jetson_ip"
if [ -z "${LANGROBO_JETSON_HOST:-}" ]; then
    if ip=$(getent hosts "$JETSON_HOST" 2>/dev/null | awk '{print $1; exit}') && [ -n "$ip" ]; then
        echo "$ip" > "$JETSON_IP_CACHE" 2>/dev/null || true
    elif [ -s "$JETSON_IP_CACHE" ]; then
        echo "jetson: $JETSON_HOST does not resolve -- using its last address $(cat "$JETSON_IP_CACHE")" >&2
        JETSON_HOST=$(cat "$JETSON_IP_CACHE")
        echo "        (fix the name: ssh rakhi24@$JETSON_HOST 'sudo systemctl restart avahi-daemon')" >&2
    fi
fi
JETSON=rakhi24@$JETSON_HOST
# IdentityAgent=none: use ~/.ssh/id_ed25519 directly. A shell whose
# SSH_AUTH_SOCK points at a dead or hung agent (a stale forwarded one, say)
# makes every ssh hang at authentication, with no error -- on 2026-09-27 that
# looked for an hour like a Jetson too loaded to log in to.
SSH="ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new -o IdentityAgent=none"

ROVER_DIR="~/rover"
TWIN_DOMAIN=42                           # the twin's ROS domain (kino_sim TWIN_DOMAIN)

ensure_local_units() {
    sudo -n systemctl start langrobo-brain 2>/dev/null \
        || systemctl start langrobo-brain 2>/dev/null || true
    echo "pi5:    brain=$(systemctl is-active langrobo-brain)"
}

reachable() { timeout 4 ping -c1 -W2 "$1" >/dev/null 2>&1; }

# Is the Jetson stack already up? Asked over ROS, NOT ssh: ssh can fail for
# reasons that say nothing about the robot (a hung agent, a slow login), and
# reading "ssh failed" as "down" would run `./rover up`, which RESTARTS the
# container. The fused pose flowing and the VLM bridge listening means every
# layer up to the last one came up.
ros_env() {
    source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
    unset ROS_DISCOVERY_SERVER FASTRTPS_DEFAULT_PROFILES_FILE
    export ROS_DOMAIN_ID=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
}
jetson_stack_up() {
    ( ros_env
      r=$(timeout 20 python3 "$(dirname "$0")/topic_rates.py" -d 3 /odom 2>/dev/null | awk '{print $2}')
      awk -v a="${r:-0}" 'BEGIN{exit !(a+0 >= 10)}' || exit 1
      c=$({ timeout 15 ros2 topic info /vision/pixel_query 2>/dev/null || true; } \
          | awk '/Subscription count/{print $3}')
      [ "${c:-0}" -ge 1 ] )
}

jetson_status() {
    if ! $SSH $JETSON "docker ps -q -f name='^rover\$' | grep -q ." 2>/dev/null; then
        echo "jetson: rover container stopped"
        return
    fi
    $SSH $JETSON 'for p in vo_node fusion2 sllidar slam_toolbox nvblox controller_server goal_exec reach_node image_bridge pixel_to_goal; do
        docker exec rover pgrep -f "$p" >/dev/null 2>&1 && s=up || s=--
        printf "%s=%s " "$p" "$s"; done; echo' 2>/dev/null | sed 's/^/jetson: /'
}

# The twin announces itself (kino_sim, latched /sim/hardware on domain 42):
# the laptop's address, cable ports, world. Asked over ROS like jetson_stack_up,
# so nothing here needs ssh into the laptop. -> the JSON, or nothing.
twin_announced() {
    ( source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
      unset ROS_DISCOVERY_SERVER FASTRTPS_DEFAULT_PROFILES_FILE
      export ROS_DOMAIN_ID=$TWIN_DOMAIN RMW_IMPLEMENTATION=rmw_fastrtps_cpp
      timeout 15 ros2 topic echo --once --qos-durability transient_local --qos-reliability reliable \
          --full-length --field data /sim/hardware std_msgs/msg/String 2>/dev/null | grep -m1 '^{' ) || true
}

twin_line() {   # the announcement, readable
    python3 -c 'import json, sys; h = json.loads(sys.argv[1]); print("twin up at", " ".join(h["hosts"]), "- world", h["world"], "- engine", h.get("engine", "?"), "- body", h.get("params_sha1", "?"))' "$1"
}

twin_up() {
    echo "twin: the simulated robot on domain $TWIN_DOMAIN (the real robot, domain 0, is not touched)"
    local hw; hw=$(twin_announced)
    if [ -z "$hw" ]; then
        echo "laptop: no twin announcing on domain $TWIN_DOMAIN. Start it ON THE LAPTOP:"
        echo "        cd ~/kino_sim && ./sim up home_real      (then run this again)"
        return 1
    fi
    echo "laptop: $(twin_line "$hw")"
    if reachable "$JETSON_HOST"; then
        # the real layers, one by one, each with its gate (~10 min with nvblox + nav2);
        # the Jetson finds the twin by itself (the same /sim/hardware)
        $SSH $JETSON "git -C ~/kino_sim pull -q --ff-only 2>/dev/null; cd ~/rover && TWIN=1 ./rover up" 2>&1 | sed 's/^/jetson: /'
    else
        echo "jetson: UNREACHABLE ($JETSON_HOST)"; return 1
    fi
    "$(dirname "$0")/twin_brain.sh" up | sed 's/^/pi5:    /'
    echo "twin: talk to it -- $(dirname "$0")/twin_brain.sh say \"Kino, what do you see?\""
    echo "      watch it    -- on the laptop: cd ~/kino_sim && ./sim rviz   (or ./sim up home_real follow=true)"
}
twin_down() {
    "$(dirname "$0")/twin_brain.sh" down | sed 's/^/pi5:    /'
    reachable "$JETSON_HOST" && $SSH $JETSON "cd ~/rover && TWIN=1 ./rover stop" 2>&1 | sed 's/^/jetson: /'
    echo "laptop: the twin's world keeps running until, on the laptop: cd ~/kino_sim && ./sim down"
    return 0
}

case "$CMD" in
twin)
    case "${2:-up}" in
        up)     [ -n "${3:-}" ] && echo "twin: the world is chosen on the laptop (./sim up <world>); '$3' ignored"
                twin_up ;;
        down)   twin_down ;;
        status)
            "$(dirname "$0")/twin_brain.sh" status | sed 's/^/pi5:    /'
            reachable "$JETSON_HOST" && $SSH $JETSON "if docker ps --format '{{.Names}}' | grep -qx rover_twin; then cd ~/rover && TWIN=1 ./rover status; else echo 'no twin stack (rover_twin container) running'; fi" 2>&1 | sed 's/^/jetson: /'
            hw=$(twin_announced)
            if [ -n "$hw" ]; then echo "laptop: $(twin_line "$hw")"
            else echo "laptop: no twin announcing on domain $TWIN_DOMAIN (on the laptop: ./sim up home_real)"; fi ;;
        *)      echo "usage: $0 twin [up|down|status]"; exit 2 ;;
    esac
    ;;
rover|up|start)
    ensure_local_units
    sudo -n systemctl start langrobo-microros 2>/dev/null \
        || systemctl start langrobo-microros 2>/dev/null || true
    echo "pi5:    microros=$(systemctl is-active langrobo-microros)"
    if ! reachable "$JETSON_HOST"; then
        echo "jetson: UNREACHABLE — is it powered on? (perception not started)"
    elif jetson_stack_up; then
        echo "jetson: stack already up — left alone (a cold restart: ssh $JETSON 'cd $ROVER_DIR && ./rover up')"
    else
        # A cold power-on: bring every layer up in order, each with its own
        # PASS/FAIL gate. Takes several minutes; streamed here so a failing
        # layer names itself. `up` restarts the container — that is why the
        # check above leaves a healthy stack alone.
        echo "jetson: running ./rover up (several minutes, each layer prints a gate)..."
        $SSH $JETSON "cd $ROVER_DIR && ./rover up" || echo "jetson: ./rover up FAILED — the last GATE above names the layer"
    fi
    # Voice lives on the Pi5 in rover mode (langrobo-voice user unit — the
    # speaker/mic owner + STT + TTS; docs/voice/PI5_VOICE.md).
    systemctl --user start langrobo-voice 2>/dev/null || true
    echo "pi5:    voice=$(systemctl --user is-active langrobo-voice)"
    echo "fleet: ROVER mode up. Talk to it (say \"Kino\") or use Telegram."
    ;;
stop)
    # Park the robot: stop the body (the Jetson's stack). No password needed.
    # The brain stays up so chat/Telegram keeps listening.
    if reachable "$JETSON_HOST"; then
        # Removes the `rover` container (every Jetson layer). A power-on
        # after this needs the full `./rover up` again.
        $SSH $JETSON "cd $ROVER_DIR && ./rover stop" || true
    fi
    # Pi5 voice stays up like the brain: parked is not deaf.
    systemctl --user start langrobo-voice 2>/dev/null || true
    echo "fleet: robot body stopped. Brain + Pi5 voice still up (Telegram/chat/voice alive)."
    echo "       Full shutdown incl. Pi5 services: $0 down"
    ;;
down)
    # Full shutdown: everything 'stop' does, PLUS this Pi5's own services
    # (brain, micro-ROS wheels). Those are systemd
    # system units, so this asks for your password once.
    if reachable "$JETSON_HOST"; then
        # Removes the `rover` container (every Jetson layer). A power-on
        # after this needs the full `./rover up` again.
        $SSH $JETSON "cd $ROVER_DIR && ./rover stop" || true
    fi
    systemctl --user stop langrobo-voice 2>/dev/null || true
    echo "pi5:    stopping brain, micro-ROS (needs sudo)..."
    sudo systemctl stop langrobo-brain langrobo-microros
    echo "pi5:    brain=$(systemctl is-active langrobo-brain)  microros=$(systemctl is-active langrobo-microros)"
    echo "fleet: fully DOWN. Bring back with: $0 rover   (or twin up)"
    echo "       (these units are enabled, so they'll also restart on next Pi5 boot)"
    ;;
status)
    echo "pi5:    brain=$(systemctl is-active langrobo-brain)  microros=$(systemctl is-active langrobo-microros)  voice=$(systemctl --user is-active langrobo-voice 2>/dev/null)"
    hw=$(twin_announced); [ -n "$hw" ] && echo "laptop: the twin is up on domain $TWIN_DOMAIN ($0 twin status)"
    if reachable "$JETSON_HOST"; then
        jetson_status
    else
        echo "jetson: unreachable"
    fi
    ;;
check)
    # Proves each link with DATA, from this Pi5, over ROS -- no ssh, so it
    # still answers when ssh does not. Each line
    # is OK or FAIL plus what to do. Read-only: nothing moves, nothing speaks.
    fails=0
    ok()   { printf "  %-34s OK    %s\n" "$1" "$2"; }
    bad()  { printf "  %-34s FAIL  %s\n" "$1" "$2"; fails=$((fails+1)); }
    ros_env
    # All rates in one process (scripts/topic_rates.py): `ros2 topic hz`
    # under-reports on this Pi5 and costs ~12 s per topic.
    RATES=$(timeout 25 python3 "$(dirname "$0")/topic_rates.py" -d 5 /odom /scan /wheel_state \
            /camera/color/image_raw/compressed 2>/dev/null || true)
    hz() { echo "$RATES" | awk -v t="$1" '$1==t{print $2}'; }
    # Thresholds are "alive", not "nominal": the probe under-counts by up to a
    # third on this Pi5 while measuring four topics (wheels 13-20 Hz of a
    # true 20). Nominal: /odom 20, /scan 10, /wheel_state 20, camera ~5.
    # Asked a few times: right after `./rover up` the ros2 daemon's graph lags the
    # newest nodes, and a first answer of 0 reported two live nodes as FAIL (2026-10-11).
    subs() {
        local c i
        for i in 1 2 3; do
            c=$({ timeout 15 ros2 topic info "$1" 2>/dev/null || true; } | awk '/Subscription count/{print $3}')
            [ "${c:-0}" -ge 1 ] 2>/dev/null && break
            [ "$i" -lt 3 ] && sleep 4
        done
        echo "$c"
    }
    at_least() { awk -v a="${1:-0}" -v b="$2" 'BEGIN{exit !(a+0 >= b+0)}'; }

    echo "Pi5"
    for u in langrobo-brain langrobo-microros; do
        [ "$(systemctl is-active $u)" = active ] && ok "$u" "" || bad "$u" "sudo systemctl restart $u"
    done
    [ "$(systemctl --user is-active langrobo-voice)" = active ] && ok "langrobo-voice" "" \
        || bad "langrobo-voice" "systemctl --user restart langrobo-voice"
    audio=$(journalctl --user -u langrobo-voice -b -o cat 2>/dev/null | grep -E "audio ready:|no audio device" | tail -1 | sed 's/.*pi5_audio_device\]: //' || true)
    case "$audio" in *"audio ready"*) ok "speaker + mic" "${audio#audio ready: }" ;;
                     *) bad "speaker + mic" "${audio:-none yet} -- switch the earbuds/speaker on (/bt-audio)" ;; esac
    # `|| true` on every probe: under set -e a probe of something that is DOWN
    # (curl's 7, grep finding nothing) ended check before it printed the FAIL
    # it exists to print -- the Jetson's OPEN_ISSUES #9 trap.
    # /status needs the bearer token when one is set; without it the reply is
    # a 401 body, which read as "LLM down".
    tok=$(sed -n 's/^LANGROBO_API_TOKEN=//p' "$(dirname "$0")/../.env" 2>/dev/null | head -1 | tr -d "\"'" || true)
    auth=(); [ -n "$tok" ] && auth=(-H "Authorization: Bearer $tok")
    st=$(curl -s -m4 "${auth[@]}" localhost:8090/status || true)
    if [ -z "$st" ]; then
        bad "brain health API :8090" "brain not answering -- journalctl -u langrobo-brain -n 50"
    elif echo "$st" | grep -q 'invalid or missing bearer token'; then
        bad "brain health API :8090" "401 -- LANGROBO_API_TOKEN in .env does not match the brain's"
    else
        echo "$st" | grep -q '"primary_available": *true' && ok "LLM (Mac Mini) via brain" "" \
            || bad "LLM (Mac Mini) via brain" "Mac server down? someone types e12b5 in a Terminal on the Mac (nothing autostarts)"
        seen=$(echo "$st" | python3 -c 'import sys,json
r=json.load(sys.stdin).get("runtime",{}); s=r.get("photo_survey") or {}
print("%s object(s) remembered; survey: %s" % (r.get("objects_remembered", "?"), ("%s photo(s), %s queued, %s error(s)" % (s.get("photos", 0), s.get("queued", 0), s.get("errors", 0))) if s.get("enabled", True) else ("OFF -- no vision-tool slot 3, the Mac needs --parallel 5: e12b5" if s.get("slot") is None else "off by design (memory = the photo log; LANGROBO_PHOTO_SURVEY=1 turns it on)")))' 2>/dev/null || true)
        [ -n "$seen" ] && printf "  %-34s --    %s\n" "object memory / photo survey" "$seen"
    fi
    # Which server: LANGROBO_LLM in .env (gemma = Mac, qwen = laptop; services/llm_profiles.py)
    llm=$(cd "$(dirname "$0")/../src/langrobo_core" && LANGROBO_LLM="$(sed -n 's/^LANGROBO_LLM=//p' ../../.env 2>/dev/null | head -1)" \
          python3 -c 'from langrobo_core.services import llm_profiles as p; r=p.profile(); print(p.server_root(), r["slots"], r["where"].replace(" ", "_"), r["start"].replace(" ", "_"))' 2>/dev/null \
          || echo "http://singireddys-mac-mini.local:8080 5 Mac_Mini e12b5")
    read -r llm_root llm_slots llm_where llm_start <<<"$llm"
    printf "  %-34s --    %s\n" "LLM server (LANGROBO_LLM)" "${llm_where//_/ } at $llm_root"
    n=$(curl -s -m4 "$llm_root/slots" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || true)
    # Mac: 5 = chat, local_agent, navigate, photo questions, the voice "is this for me?" check
    # (CLAUDE.md rule 2). Laptop: 4 -- the voice check is off since 2026-10-12.
    at_least "$n" "$llm_slots" && ok "LLM KV slots" "$n (need $llm_slots)" || bad "LLM KV slots" "${n:-none} -- start it: ${llm_start//_/ }; see OPERATIONS.md"
    m=$(curl -s -m4 localhost:8091/mode || true)
    case "$m" in *'"manual": false'*|*'"manual":false'*) ok "teleop" "AUTO" ;;
                 "") bad "teleop :8091" "not answering (~/langrobo_teleop/teleop_web.py)" ;;
                 *) bad "teleop" "MANUAL -- it streams zeros and cancels nav2; flip to AUTO" ;; esac

    echo "Jetson (seen over ROS from here)"
    r=$(hz /odom);      at_least "$r" 10 && ok "fused pose /odom" "${r} Hz" || bad "fused pose /odom" "${r:-none} -- ./rover fused (or fleet.sh rover)"
    r=$(hz /scan);      at_least "$r" 5  && ok "lidar /scan" "${r} Hz" || bad "lidar /scan" "${r:-none} -- ./rover lidar"
    r=$(hz /wheel_state); at_least "$r" 8  && ok "ESP32 wheels /wheel_state" "${r} Hz" || bad "ESP32 wheels /wheel_state" "${r:-none} -- ESP32 powered? langrobo-microros up?"
    r=$(hz /camera/color/image_raw/compressed); at_least "$r" 1 && ok "camera for look()" "${r} Hz" || bad "camera for look()" "${r:-none} -- ./rover vlm"
    for t in /vision/pixel_query:"depth grounding (pixel_to_goal)":"./rover vlm" \
             /goal_exec/goal:"exact moves (goal_exec)":"./rover nav" \
             /reach/goal:"navigation (reach + nav2)":"./rover nav" \
             /follow/start:"follow me (follow_node + YOLO)":"./rover follow"; do
        IFS=: read -r topic name fix <<<"$t"
        c=$(subs "$topic"); at_least "$c" 1 && ok "$name" "" || bad "$name" "no listener on $topic -- $fix"
    done
    if { timeout 10 ros2 run tf2_ros tf2_echo map odom 2>/dev/null || true; } | grep -q Translation; then ok "slam map -> odom" ""
    else bad "slam map -> odom" "./rover slam (RViz shows nothing without it)"; fi

    echo "Studio / LangSmith"
    [ "$(curl -s -m3 http://127.0.0.1:2024/ok)" = '{"ok":true}' ] && ok "LangGraph Studio :2024" "" \
        || printf "  %-34s --    %s\n" "LangGraph Studio :2024" "not running (optional): ./scripts/start_studio.sh"
    grep -qE '^LANGSMITH_API_KEY=.+' "$(dirname "$0")/../.env" 2>/dev/null && ok "LangSmith tracing key" "project from LANGCHAIN_PROJECT" \
        || printf "  %-34s --    %s\n" "LangSmith tracing key" "none in .env (optional)"

    echo
    if [ "$fails" -eq 0 ]; then echo "check: ALL OK — say \"Kino, ...\" or message it on Telegram."
    else echo "check: $fails FAIL(s) — each line says what to run."; exit 1; fi
    ;;
*)
    echo "usage: $0 [rover|stop|down|status|check|twin]   (no argument = rover)"
    echo "  rover  start the REAL body -- the default (also: up, start): Pi5 micro-ROS wheels + Jetson ./rover up; Pi5 voice"
    echo "  stop   stop the robot body; keep the brain (Telegram) + voice alive"
    echo "  down   full shutdown incl. Pi5 brain/microros (asks sudo)"
    echo "  status show what's up everywhere"
    echo "  twin   (only when asked) the twin [up|down|status]: after ./sim up on the laptop, the robot's"
    echo "         hardware simulated in Isaac Sim, the rover's whole Jetson stack on it (TWIN=1 ./rover up) + a 2nd brain, domain 42"
    echo "  check  prove every link works, with data (read-only)"
    exit 2
    ;;
esac
