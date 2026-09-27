#!/usr/bin/env bash
# LangRobo fleet launcher — run on the Pi5. One command to bring up the robot
# in either body:
#
#   ./scripts/fleet.sh rover    # REAL body: Pi5 micro-ROS agent (ESP32 wheels)
#                               #   + the Jetson's ~/rover stack, via `./rover up`
#                               #   (D555 + lidar + fused pose + slam + nvblox +
#                               #   nav2 + the VLM bridge + Studio + laptop RViz).
#                               #   Voice is the Pi5's own trio.
#   ./scripts/fleet.sh sim      # SIMULATION body: laptop Gazebo sim (+ its nav2)
#                               #   + Pi5 voice. The Jetson is not used.
#   ./scripts/fleet.sh stop     # park the robot: stop the body, keep brain up
#   ./scripts/fleet.sh down     # full shutdown incl. this Pi5's services (sudo)
#   ./scripts/fleet.sh status   # who's up, everywhere
#   ./scripts/fleet.sh check    # is it WORKING: every link proved with data, from here
#
# The brain (langrobo-brain) runs here in both modes. Each machine can still
# be started by itself:
#   laptop:  /workspace/ros2_ws/src/rover_sim/rover_bringup/scripts/fleet_sim.sh
#   jetson:  cd ~/rover && ./rover up      (or one layer: ./rover camera, ...)
#
# The Jetson's live repo is ~/rover (container `rover`). The older ~/robot
# roles (`ai_stack` voice, `isaac_ros` perception) are retired: those
# containers no longer exist, so this script no longer calls them.
#
# Machines are reached by mDNS name (NETWORKING.md) with passwordless ssh.

set -eo pipefail
CMD="${1:-status}"

# mDNS names by default; override when mDNS flakes (it does, transiently):
#   LANGROBO_LAPTOP_HOST=192.168.1.12 ./scripts/fleet.sh sim
LAPTOP_HOST="${LANGROBO_LAPTOP_HOST:-rakhi24.local}"
JETSON_HOST="${LANGROBO_JETSON_HOST:-rakhi-jetson.local}"
LAPTOP=rakhi24@$LAPTOP_HOST
JETSON=rakhi24@$JETSON_HOST
# IdentityAgent=none: use ~/.ssh/id_ed25519 directly. A shell whose
# SSH_AUTH_SOCK points at a dead or hung agent (a stale forwarded one, say)
# makes every ssh hang at authentication, with no error -- on 2026-09-27 that
# looked for an hour like a Jetson too loaded to log in to.
SSH="ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new -o IdentityAgent=none"

SIM_SCRIPT=/workspace/ros2_ws/src/rover_sim/rover_bringup/scripts/fleet_sim.sh
ROVER_DIR="~/rover"
BRAIN_ENV=/home/rakhi24/.langrobo/brain.env

# Which body (rover|sim) the brain's cmd_vel should drive — see ros2_bridge.py
# and CLAUDE.md "Simulation laptop". Only restarts the brain when the body is
# actually changing, so re-running `fleet.sh rover` while already in rover
# mode doesn't interrupt a live conversation.
set_body() {
    local body="$1" current=""
    [ -f "$BRAIN_ENV" ] && current=$(sed -n 's/^ROBOT_BODY=//p' "$BRAIN_ENV" | head -1)
    current="${current:-rover}"     # run_brain.sh's default when the line is absent
    if [ "$current" = "$body" ]; then
        return 0
    fi
    mkdir -p "$(dirname "$BRAIN_ENV")"
    # Edit the one line, never rewrite the file: it also holds the turn
    # calibration (LANGROBO_STEADY_ANGULAR_VEL) and the notes explaining it.
    if grep -q '^ROBOT_BODY=' "$BRAIN_ENV" 2>/dev/null; then
        sed -i "s/^ROBOT_BODY=.*/ROBOT_BODY=$body/" "$BRAIN_ENV"
    else
        echo "ROBOT_BODY=$body" >> "$BRAIN_ENV"
    fi
    if sudo -n systemctl restart langrobo-brain 2>/dev/null \
        || systemctl restart langrobo-brain 2>/dev/null; then
        echo "pi5:    robot_body switched to '$body' (brain restarted)"
    else
        echo "pi5:    robot_body set to '$body' in $BRAIN_ENV, but the brain"
        echo "        restart needs sudo — run: sudo systemctl restart langrobo-brain"
    fi
}

ensure_local_units() {
    sudo -n systemctl start langrobo-discovery 2>/dev/null \
        || systemctl start langrobo-discovery 2>/dev/null || true
    sudo -n systemctl start langrobo-brain 2>/dev/null \
        || systemctl start langrobo-brain 2>/dev/null || true
    echo "pi5:    discovery=$(systemctl is-active langrobo-discovery)  brain=$(systemctl is-active langrobo-brain)"
}

reachable() { timeout 4 ping -c1 -W2 "$1" >/dev/null 2>&1; }

# Is the Jetson stack already up? Asked over ROS, NOT ssh: ssh can fail for
# reasons that say nothing about the robot (a hung agent, a slow login), and
# reading "ssh failed" as "down" would run `./rover up`, which RESTARTS the
# container. The fused pose flowing and the VLM bridge listening means every
# layer up to the last one came up.
ros_env() {
    source /opt/ros/jazzy/setup.bash
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

case "$CMD" in
sim)
    set_body sim
    ensure_local_units
    if reachable "$LAPTOP_HOST"; then
        echo "laptop: starting sim..."
        $SSH $LAPTOP "$SIM_SCRIPT start" || echo "laptop: sim start FAILED"
    else
        echo "laptop: UNREACHABLE — is it powered on and on the wifi? (sim not started)"
    fi
    # Voice stays on the Pi5 (langrobo-voice). The Jetson's old sim roles
    # (ai_stack voice, isaac_ros perception on the sim's /cam_1) are retired;
    # the sim's own Nav2 + slam_toolbox drive, look() has no Jetson frame.
    systemctl --user start langrobo-voice 2>/dev/null || true
    echo "pi5:    voice=$(systemctl --user is-active langrobo-voice)"
    echo "fleet: SIM mode up. Nav2 needs ~1 min in the house world; check: $0 status"
    ;;
rover)
    set_body rover
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
    # speaker/mic owner + STT + TTS; PI5_VOICE.md).
    systemctl --user start langrobo-voice 2>/dev/null || true
    echo "pi5:    voice=$(systemctl --user is-active langrobo-voice)"
    echo "fleet: ROVER mode up. Talk to it (say \"Mitra\") or use Telegram."
    ;;
stop)
    # Park the robot: stop the body (sim + Jetson roles). No password needed.
    # Brain + discovery stay up so chat/Telegram keeps listening.
    if reachable "$LAPTOP_HOST"; then
        $SSH $LAPTOP "$SIM_SCRIPT stop" || true
    fi
    if reachable "$JETSON_HOST"; then
        # Removes the `rover` container (every Jetson layer). A power-on
        # after this needs the full `./rover up` again.
        $SSH $JETSON "cd $ROVER_DIR && ./rover stop" || true
    fi
    # Pi5 voice stays up like the brain: parked is not deaf.
    systemctl --user start langrobo-voice 2>/dev/null || true
    echo "fleet: robot body stopped. Brain + discovery + Pi5 voice still up (Telegram/chat/voice alive)."
    echo "       Full shutdown incl. Pi5 services: $0 down"
    ;;
down)
    # Full shutdown: everything 'stop' does, PLUS this Pi5's own services
    # (brain, micro-ROS wheels, discovery meeting point). Those are systemd
    # system units, so this asks for your password once.
    if reachable "$LAPTOP_HOST"; then
        $SSH $LAPTOP "$SIM_SCRIPT stop" || true
    fi
    if reachable "$JETSON_HOST"; then
        # Removes the `rover` container (every Jetson layer). A power-on
        # after this needs the full `./rover up` again.
        $SSH $JETSON "cd $ROVER_DIR && ./rover stop" || true
    fi
    systemctl --user stop langrobo-voice 2>/dev/null || true
    echo "pi5:    stopping brain, micro-ROS, discovery (needs sudo)..."
    sudo systemctl stop langrobo-brain langrobo-microros langrobo-discovery
    echo "pi5:    discovery=$(systemctl is-active langrobo-discovery)  brain=$(systemctl is-active langrobo-brain)  microros=$(systemctl is-active langrobo-microros)"
    echo "fleet: fully DOWN. Bring back with: $0 sim   (or rover)"
    echo "       (these units are enabled, so they'll also restart on next Pi5 boot)"
    ;;
status)
    echo "pi5:    discovery=$(systemctl is-active langrobo-discovery)  brain=$(systemctl is-active langrobo-brain)  microros=$(systemctl is-active langrobo-microros)  voice=$(systemctl --user is-active langrobo-voice 2>/dev/null)"
    if reachable "$LAPTOP_HOST"; then
        $SSH $LAPTOP "$SIM_SCRIPT status" 2>/dev/null || true
    else
        echo "laptop: unreachable"
    fi
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
    subs() { { timeout 15 ros2 topic info "$1" 2>/dev/null || true; } | awk '/Subscription count/{print $3}'; }
    at_least() { awk -v a="${1:-0}" -v b="$2" 'BEGIN{exit !(a+0 >= b+0)}'; }

    echo "Pi5"
    for u in langrobo-brain langrobo-microros; do
        [ "$(systemctl is-active $u)" = active ] && ok "$u" "" || bad "$u" "sudo systemctl restart $u"
    done
    [ "$(systemctl --user is-active langrobo-voice)" = active ] && ok "langrobo-voice" "" \
        || bad "langrobo-voice" "systemctl --user restart langrobo-voice"
    audio=$(journalctl --user -u langrobo-voice -b -o cat 2>/dev/null | grep -E "audio ready:|no audio device" | tail -1 | sed 's/.*pi5_audio_device\]: //')
    case "$audio" in *"audio ready"*) ok "speaker + mic" "${audio#audio ready: }" ;;
                     *) bad "speaker + mic" "${audio:-none yet} -- switch the earbuds/speaker on (/bt-audio)" ;; esac
    st=$(curl -s -m4 localhost:8090/status)
    if [ -z "$st" ]; then
        bad "brain health API :8090" "brain not answering -- journalctl -u langrobo-brain -n 50"
    else
        echo "$st" | grep -q '"primary_available": *true' && ok "LLM (Mac Mini) via brain" "" \
            || bad "LLM (Mac Mini) via brain" "check llama.cpp on singireddys-mac-mini.local:8080"
    fi
    n=$(curl -s -m4 http://singireddys-mac-mini.local:8080/slots | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
    at_least "$n" 3 && ok "LLM KV slots" "$n (need >= 3)" || bad "LLM KV slots" "${n:-none} -- restart llama.cpp with --jinja --parallel 3"
    m=$(curl -s -m4 localhost:8091/mode)
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
             /reach/goal:"navigation (reach + nav2)":"./rover nav"; do
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
    if [ "$fails" -eq 0 ]; then echo "check: ALL OK — say \"Mitra, ...\" or message it on Telegram."
    else echo "check: $fails FAIL(s) — each line says what to run."; exit 1; fi
    ;;
*)
    echo "usage: $0 {sim|rover|stop|down|status|check}"
    echo "  rover  start real body (Pi5 micro-ROS wheels + Jetson ./rover up; Pi5 voice)"
    echo "  sim    start simulation body (laptop Gazebo+Nav2; Pi5 voice)"
    echo "  stop   stop the robot body; keep brain + discovery (Telegram) alive"
    echo "  down   full shutdown incl. Pi5 brain/discovery/microros (asks sudo)"
    echo "  status show what's up everywhere"
    echo "  check  prove every link works, with data (read-only)"
    exit 2
    ;;
esac
