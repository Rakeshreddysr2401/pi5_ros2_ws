// ============================================================
//  Rover Firmware v2  —  micro-ROS2 on ESP32   (BTS7960 + encoders)
//
//  Hardware : ESP32 DevKit V1, 2x BTS7960 (IBT-2), 4x Rhino GB37 encoder DC
//             motors (12V, 180/193 RPM, 1:30 gear), differential drive
//             (2 motors per side, paralleled onto ONE BTS7960 per side).
//  Transport: WiFi UDP -> micro_ros_agent on Pi5 (192.168.1.16:8888)
//
//  ── ROS INTERFACE ───────────────────────────────────────────────────────────
//  IN   /cmd_vel     geometry_msgs/Twist    target body vx, wz   (nav2 / teleop)
//  OUT  /wheel_odom  nav_msgs/Odometry      encoder odom -> Jetson EKF (odom1)
//       Both use BEST_EFFORT QoS — reliable stalls over micro-ROS WiFi.
//       Frames: header=odom, child=base_link. Twist (vx, vyaw) is what the EKF
//       fuses; pose is integrated too. This node does NOT publish any TF — the
//       robot_localization EKF owns odom->base_link (see config/ekf.yaml).
//
//  ── CONTROL ──────────────────────────────────────────────────────────────────
//  Per-side PID velocity loop off the wheel encoders (avg of front+rear each
//  side). 500 ms /cmd_vel watchdog. WiFi agent-reconnect state machine.
//
//  ── TOOLCHAIN (ESP32 Arduino core 3.x + micro_ros_arduino) ──────────────────
//   * core 3.x LEDC is by-PIN: ledcAttach(pin,freq,res) + ledcWrite(pin,duty).
//   * time-sync API varies; call is behind a compile guard (see createEntities).
//   * EXECUTE_EVERY_N_MS uses Arduino millis() (uxr_millis not always exported).
//   * Encoder internal pull-ups OFF: input-only pads (34/35/36/39) can't have
//     them (harmless boot errors otherwise); GB37 encoders are push-pull @3V3.
//
//  ── WIRING ───────────────────────────────────────────────────────────────────
//    Left  BTS7960 : RPWM=18 LPWM=19 R_EN=21 L_EN=22
//    Right BTS7960 : RPWM=23 LPWM=5  R_EN=27 L_EN=13
//    Encoders (A,B): Left-Front 34/35   Left-Rear  36/39
//                    Right-Front 32/33  Right-Rear 25/26
//    Encoder power : Blue=3V3, Black=GND, Green=C1(A), Yellow=C2(B)
//
//  LIBRARIES: micro_ros_arduino (jazzy) + ESP32Encoder (madhephaestus)
// ============================================================

#include <WiFi.h>
#include <ArduinoOTA.h>
#include <ESP32Encoder.h>
#include <math.h>

#include <micro_ros_arduino.h>
#include <rcl/rcl.h>
#include <rcl/error_handling.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>
#include <rmw_microros/rmw_microros.h>
#include <geometry_msgs/msg/twist.h>
#include <geometry_msgs/msg/vector3.h>
#include <nav_msgs/msg/odometry.h>

// ── WiFi + agent ─────────────────────────────────────────────────────────────
//  Fill WIFI_PASS locally before flashing. Do NOT commit real credentials.
const char* WIFI_SSID  = "Airtel_Singireddy's";
const char* WIFI_PASS  = "YOUR_WIFI_PASSWORD";   // <-- set locally
const char* AGENT_IP   = "192.168.1.16";          // Pi5 wlan0
const uint16_t AGENT_PORT = 8888;
const char* OTA_HOSTNAME = "rover-esp32";
// OTA left UNAUTHENTICATED for bench work — set a password before deployment:
// #define OTA_PASSWORD "choose-something"

// ── Motor driver pins (BTS7960) ──────────────────────────────────────────────
#define L_RPWM 18
#define L_LPWM 19
#define L_REN  21
#define L_LEN  22
#define R_RPWM 23
#define R_LPWM 5
#define R_REN  27
#define R_LEN  13

// ── Encoder pins — ALL FOUR wheels (A,B per wheel) ───────────────────────────
#define ENC_LF_A 34
#define ENC_LF_B 35
#define ENC_LR_A 36   // VP — input-only
#define ENC_LR_B 39   // VN — input-only
#define ENC_RF_A 32
#define ENC_RF_B 33
#define ENC_RR_A 25
#define ENC_RR_B 26

// ── PWM (core 3.x: attach per pin, write per pin) ────────────────────────────
#define PWM_FREQ 1000      // Hz
#define PWM_RES  8         // 8-bit -> 0..255

// ╔════════════════════════ CALIBRATION ═══════════════════════════════════════╗
#define WHEEL_DIAMETER_M 0.085f   // 85 mm tyre OD
#define WHEEL_BASE_M     0.34f    // 34 cm between L<->R wheel centres
#define ENCODER_CPR      1560.0f  // 13 PPR * 4 (quad) * 30 gear = counts / wheel rev
#define MAX_WHEEL_VEL    0.86f    // m/s at full PWM = (193/60)*PI*0.085

// Direction flags — SET FROM BENCH TEST. Right side is mounted mirror-image, so
// both its motor AND its encoders are inverted vs the left (verified 2026-08-07:
// forward cmd drove left fwd, right backward; right encoders count -ve on fwd).
#define L_MOTOR_DIR (+1)   // +cmd spins LEFT side forward
#define R_MOTOR_DIR (-1)   // +cmd spins RIGHT side forward  (flipped)
#define ENC_LF_DIR (+1)    // forward motion => +count
#define ENC_LR_DIR (+1)
#define ENC_RF_DIR (-1)    // right encoders inverted (flipped)
#define ENC_RR_DIR (-1)
// ╚═════════════════════════════════════════════════════════════════════════════╝

#define WHEEL_CIRC       (float)(M_PI * WHEEL_DIAMETER_M)
#define METRES_PER_COUNT (WHEEL_CIRC / ENCODER_CPR)

// ── Control loop + PID ───────────────────────────────────────────────────────
#define CONTROL_HZ      50.0f
#define CONTROL_DT      (1.0f / CONTROL_HZ)
#define CMD_TIMEOUT_MS  500
// PID gains — RUNTIME-TUNABLE live via /pid_gains (Vector3 x=Kp y=Ki z=minMoveDuty),
// so tuning needs no reflash. Defaults lowered from (1.5/4.0/0.12): the old high Kp +
// hard breakaway step caused a limit cycle (wheel overshoots -> Kp drives it negative ->
// "goes forward then comes back"), worst when lifted/unloaded.
float Kp  = 0.6f;
float Ki  = 2.0f;
float Kff = 1.0f / MAX_WHEEL_VEL;
float minMoveDuty = 0.08f;   // static-friction breakaway (0 = pure PI + feedforward)

#define DEBUG_SERIAL 1     // 1 = print IN/OUT at ~2 Hz on Serial @115200

// ── Agent connection state machine ──────────────────────────────────────────
enum AgentState { WAITING_AGENT, AGENT_AVAILABLE, AGENT_CONNECTED, AGENT_DISCONNECTED };
AgentState agentState = WAITING_AGENT;

//  Plain Arduino millis(). Unsigned subtraction handles rollover. last=0 fires
//  the first call immediately.
#define EXECUTE_EVERY_N_MS(MS, X) do {         \
    static uint32_t last = 0;                  \
    uint32_t now = millis();                   \
    if ((now - last) >= (MS)) { last = now; X; } \
} while (0)

// ── Encoders ─────────────────────────────────────────────────────────────────
ESP32Encoder encLF, encLR, encRF, encRR;
long lastLF = 0, lastLR = 0, lastRF = 0, lastRR = 0;

// ── Command + PID state ──────────────────────────────────────────────────────
volatile float targetVx = 0.0f, targetWz = 0.0f;
unsigned long lastCmdMs = 0;
float integL = 0.0f, integR = 0.0f;

// ── Odometry pose ────────────────────────────────────────────────────────────
float odomX = 0.0f, odomY = 0.0f, odomTh = 0.0f;

// ── micro-ROS entities ───────────────────────────────────────────────────────
rcl_subscription_t cmdVelSub;
rcl_subscription_t pidGainsSub;      // live PID tuning (Vector3 x=Kp y=Ki z=minMoveDuty)
rcl_publisher_t    odomPub;
rcl_publisher_t    wheelStatePub;    // small telemetry: x=velL y=velR z=cmd vx (crosses WiFi)
rcl_timer_t        controlTimer;
geometry_msgs__msg__Twist   twistMsg;
geometry_msgs__msg__Vector3 pidGainsMsg;
geometry_msgs__msg__Vector3 wheelStateMsg;
nav_msgs__msg__Odometry     odomMsg;
rclc_executor_t executor;
rclc_support_t  support;
rcl_allocator_t allocator;
rcl_node_t      node;
bool timeSynced = false;

// ── BTS7960 drive: duty -1..+1 for one side ──────────────────────────────────
void driveSide(int pinR, int pinL, int dir, float duty) {
    duty *= dir;
    if (duty >  1.0f) duty =  1.0f;
    if (duty < -1.0f) duty = -1.0f;
    int pwm = (int)(fabsf(duty) * 255.0f);
    if (duty > 0.001f)      { ledcWrite(pinR, pwm); ledcWrite(pinL, 0);   }
    else if (duty < -0.001f){ ledcWrite(pinR, 0);   ledcWrite(pinL, pwm); }
    else                    { ledcWrite(pinR, 0);   ledcWrite(pinL, 0);   }
}

void stopMotors() {
    ledcWrite(L_RPWM, 0); ledcWrite(L_LPWM, 0);
    ledcWrite(R_RPWM, 0); ledcWrite(R_LPWM, 0);
}

// ── PID (velocity) for one side -> duty ──────────────────────────────────────
float pidStep(float target, float meas, float &integ) {
    if (fabsf(target) < 0.01f) { integ = 0.0f; return 0.0f; }
    float err = target - meas;
    integ += err * CONTROL_DT;
    float ilim = 1.0f / Ki;                 // anti-windup: |Ki*integ| <= 1
    if (integ >  ilim) integ =  ilim;
    if (integ < -ilim) integ = -ilim;
    float out = Kff * target + Kp * err + Ki * integ;
    out += (target > 0.0f) ? minMoveDuty : -minMoveDuty;
    if (out >  1.0f) out =  1.0f;
    if (out < -1.0f) out = -1.0f;
    return out;
}

// ── Control + odom timer @ CONTROL_HZ ────────────────────────────────────────
void controlCb(rcl_timer_t* timer, int64_t /*last*/) {
    if (!timer) return;

    // 1) measure per-side velocity — average both encoders on each side
    long cLF = (long)encLF.getCount() * ENC_LF_DIR;
    long cLR = (long)encLR.getCount() * ENC_LR_DIR;
    long cRF = (long)encRF.getCount() * ENC_RF_DIR;
    long cRR = (long)encRR.getCount() * ENC_RR_DIR;
    long dLF = cLF - lastLF;  lastLF = cLF;
    long dLR = cLR - lastLR;  lastLR = cLR;
    long dRF = cRF - lastRF;  lastRF = cRF;
    long dRR = cRR - lastRR;  lastRR = cRR;
    float distL = 0.5f * (dLF + dLR) * METRES_PER_COUNT;
    float distR = 0.5f * (dRF + dRR) * METRES_PER_COUNT;
    float velL  = distL / CONTROL_DT;
    float velR  = distR / CONTROL_DT;

    // 2) watchdog: silence from the Pi5 -> stop (log the transition once)
    static bool wdStopped = false;
    float tvx = targetVx, twz = targetWz;
    if (millis() - lastCmdMs > CMD_TIMEOUT_MS) {
        tvx = 0.0f; twz = 0.0f;
        if (!wdStopped) { wdStopped = true; Serial.println("[WD] /cmd_vel stale -> stop"); }
    } else {
        wdStopped = false;
    }

    // 3) target -> per-wheel setpoints -> PID -> BTS7960
    float wTargetL = tvx - twz * WHEEL_BASE_M * 0.5f;
    float wTargetR = tvx + twz * WHEEL_BASE_M * 0.5f;
    driveSide(L_RPWM, L_LPWM, L_MOTOR_DIR, pidStep(wTargetL, velL, integL));
    driveSide(R_RPWM, R_LPWM, R_MOTOR_DIR, pidStep(wTargetR, velR, integR));

    // 4) integrate odometry from MEASURED wheel travel
    float ds  = 0.5f * (distL + distR);
    float dth = (distR - distL) / WHEEL_BASE_M;
    odomX  += ds * cosf(odomTh + 0.5f * dth);
    odomY  += ds * sinf(odomTh + 0.5f * dth);
    odomTh += dth;

    // 5) publish /wheel_odom (twist = vx, vyaw = what the EKF fuses)
    int64_t ns = rmw_uros_epoch_nanos();
    odomMsg.header.stamp.sec     = (int32_t)(ns / 1000000000LL);
    odomMsg.header.stamp.nanosec = (uint32_t)(ns % 1000000000LL);
    odomMsg.pose.pose.position.x = odomX;
    odomMsg.pose.pose.position.y = odomY;
    odomMsg.pose.pose.orientation.z = sinf(odomTh * 0.5f);
    odomMsg.pose.pose.orientation.w = cosf(odomTh * 0.5f);
    odomMsg.twist.twist.linear.x  = ds  / CONTROL_DT;
    odomMsg.twist.twist.angular.z = dth / CONTROL_DT;
    rcl_publish(&odomPub, &odomMsg, NULL);

    // small telemetry that DOES cross the WiFi link (Odometry is too big): lets the
    // host watch each wheel's real speed/direction + the commanded vx for tuning.
    wheelStateMsg.x = velL;
    wheelStateMsg.y = velR;
    wheelStateMsg.z = tvx;
    rcl_publish(&wheelStatePub, &wheelStateMsg, NULL);

#if DEBUG_SERIAL
    static uint16_t dbg = 0;               // ~2 Hz: IN (tgt) + OUT (vel/enc)
    if (++dbg >= 25) {
        dbg = 0;
        Serial.printf("IN tgt vx=%.2f wz=%.2f | OUT velL=%.2f velR=%.2f odom(x=%.2f y=%.2f th=%.2f) "
                      "| enc LF=%ld LR=%ld RF=%ld RR=%ld\n",
                      tvx, twz, velL, velR, odomX, odomY, odomTh, cLF, cLR, cRF, cRR);
    }
#endif
}

// ── /cmd_vel callback ────────────────────────────────────────────────────────
void cmdVelCb(const void* msgIn) {
    const geometry_msgs__msg__Twist* m = (const geometry_msgs__msg__Twist*)msgIn;
    targetVx = (float)m->linear.x;
    targetWz = (float)m->angular.z;
    lastCmdMs = millis();
}

// Live PID tuning — no reflash needed. Vector3: x=Kp, y=Ki, z=minMoveDuty.
void pidGainsCb(const void* msgIn) {
    const geometry_msgs__msg__Vector3* m = (const geometry_msgs__msg__Vector3*)msgIn;
    Kp = (float)m->x;
    Ki = (float)m->y;
    minMoveDuty = (float)m->z;
    integL = integR = 0.0f;   // reset windup on retune
    Serial.printf("[PID] set Kp=%.3f Ki=%.3f minDuty=%.3f\n", Kp, Ki, minMoveDuty);
}

// ── Odometry message static setup (frame ids + covariance) ───────────────────
void initOdomMsg() {
    static char odom_frame[]  = "odom";
    static char base_frame[]  = "base_link";
    odomMsg.header.frame_id.data     = odom_frame;
    odomMsg.header.frame_id.size     = strlen(odom_frame);
    odomMsg.header.frame_id.capacity = sizeof(odom_frame);
    odomMsg.child_frame_id.data      = base_frame;
    odomMsg.child_frame_id.size      = strlen(base_frame);
    odomMsg.child_frame_id.capacity  = sizeof(base_frame);
    for (int i = 0; i < 36; i++) { odomMsg.pose.covariance[i] = 0.0; odomMsg.twist.covariance[i] = 0.0; }
    odomMsg.pose.covariance[0]  = 0.02;  odomMsg.pose.covariance[7]  = 0.02;   // x, y
    odomMsg.pose.covariance[14] = 1e6;   odomMsg.pose.covariance[21] = 1e6;
    odomMsg.pose.covariance[28] = 1e6;   odomMsg.pose.covariance[35] = 0.05;   // yaw
    odomMsg.twist.covariance[0]  = 0.01;                                        // vx
    odomMsg.twist.covariance[7]  = 1e6;  odomMsg.twist.covariance[14] = 1e6;
    odomMsg.twist.covariance[21] = 1e6;  odomMsg.twist.covariance[28] = 1e6;
    odomMsg.twist.covariance[35] = 0.02;                                        // vyaw
    odomMsg.pose.pose.orientation.w = 1.0;
}

// ── micro-ROS entity lifecycle ───────────────────────────────────────────────
bool createEntities() {
    allocator = rcl_get_default_allocator();
    if (rclc_support_init(&support, 0, NULL, &allocator) != RCL_RET_OK) return false;
    rmw_context_t* rmw_context = rcl_context_get_rmw_context(&support.context);
    (void) rmw_uros_set_context_entity_destroy_session_timeout(rmw_context, 0);

    if (rclc_node_init_default(&node, "rover_esp32", "", &support) != RCL_RET_OK) return false;

    // BEST_EFFORT both ways — reliable QoS stalls over the micro-ROS WiFi link.
    if (rclc_subscription_init_best_effort(&cmdVelSub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(geometry_msgs, msg, Twist), "/cmd_vel") != RCL_RET_OK) return false;
    if (rclc_publisher_init_best_effort(&odomPub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(nav_msgs, msg, Odometry), "/wheel_odom") != RCL_RET_OK) return false;
    if (rclc_publisher_init_best_effort(&wheelStatePub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(geometry_msgs, msg, Vector3), "/wheel_state") != RCL_RET_OK) return false;
    if (rclc_subscription_init_best_effort(&pidGainsSub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(geometry_msgs, msg, Vector3), "/pid_gains") != RCL_RET_OK) return false;
    if (rclc_timer_init_default(&controlTimer, &support,
            RCL_MS_TO_NS((int)(1000.0f / CONTROL_HZ)), controlCb) != RCL_RET_OK) return false;

    rclc_executor_init(&executor, &support.context, 3, &allocator);   // 2 subs + 1 timer
    rclc_executor_add_subscription(&executor, &cmdVelSub, &twistMsg, &cmdVelCb, ON_NEW_DATA);
    rclc_executor_add_subscription(&executor, &pidGainsSub, &pidGainsMsg, &pidGainsCb, ON_NEW_DATA);
    rclc_executor_add_timer(&executor, &controlTimer);

    // Align stamps with the agent clock so robot_localization accepts them.
#if defined(RMW_UROS_SYNC_SESSION) || __has_include(<rmw_microros/time_sync.h>)
    timeSynced = (rmw_uros_sync_session(1000) == RMW_RET_OK);
#else
    timeSynced = false;
#endif
    Serial.printf("[uROS] time sync %s\n", timeSynced ? "OK" : "off (board-time stamps)");

    // fresh baseline so the first tick isn't a huge accumulated delta
    lastLF = (long)encLF.getCount() * ENC_LF_DIR;
    lastLR = (long)encLR.getCount() * ENC_LR_DIR;
    lastRF = (long)encRF.getCount() * ENC_RF_DIR;
    lastRR = (long)encRR.getCount() * ENC_RR_DIR;
    integL = integR = 0.0f;
    lastCmdMs = millis();
    Serial.println("[uROS] entities live — IN /cmd_vel /pid_gains, OUT /wheel_odom /wheel_state");
    return true;
}

void destroyEntities() {
    rcl_subscription_fini(&cmdVelSub, &node);
    rcl_subscription_fini(&pidGainsSub, &node);
    rcl_publisher_fini(&odomPub, &node);
    rcl_publisher_fini(&wheelStatePub, &node);
    rcl_timer_fini(&controlTimer);
    rclc_executor_fini(&executor);
    rcl_node_fini(&node);
    rclc_support_fini(&support);
    Serial.println("[uROS] entities destroyed");
}

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("=== Rover ESP32 v2 (BTS7960 + encoders) starting ===");

    // BTS7960 enables — hold both HIGH so each half-bridge is active
    pinMode(L_REN, OUTPUT); pinMode(L_LEN, OUTPUT);
    pinMode(R_REN, OUTPUT); pinMode(R_LEN, OUTPUT);
    digitalWrite(L_REN, HIGH); digitalWrite(L_LEN, HIGH);
    digitalWrite(R_REN, HIGH); digitalWrite(R_LEN, HIGH);

    // PWM (core 3.x): ledcAttach(pin, freq, resolution). Verify each.
    bool pwmOK = true;
    pwmOK &= ledcAttach(L_RPWM, PWM_FREQ, PWM_RES);
    pwmOK &= ledcAttach(L_LPWM, PWM_FREQ, PWM_RES);
    pwmOK &= ledcAttach(R_RPWM, PWM_FREQ, PWM_RES);
    pwmOK &= ledcAttach(R_LPWM, PWM_FREQ, PWM_RES);
    Serial.printf("[PWM] attach %s\n", pwmOK ? "OK" : "FAILED — motors won't drive");
    stopMotors();

    // Encoders (PCNT hardware quadrature). Internal pull-ups OFF (input-only
    // pads can't have them; GB37 encoders are push-pull at 3V3).
    ESP32Encoder::useInternalWeakPullResistors = puType::none;
    encLF.attachFullQuad(ENC_LF_A, ENC_LF_B);
    encLR.attachFullQuad(ENC_LR_A, ENC_LR_B);
    encRF.attachFullQuad(ENC_RF_A, ENC_RF_B);
    encRR.attachFullQuad(ENC_RR_A, ENC_RR_B);
    encLF.clearCount(); encLR.clearCount();
    encRF.clearCount(); encRR.clearCount();
    Serial.println("[ENC] 4x quadrature attached");

    initOdomMsg();

    Serial.printf("[WiFi] agent %s:%d\n", AGENT_IP, AGENT_PORT);
    set_microros_wifi_transports((char*)WIFI_SSID, (char*)WIFI_PASS, (char*)AGENT_IP, AGENT_PORT);
    WiFi.setSleep(false);   // modem sleep adds ~100 ms latency to every cmd

    ArduinoOTA.setHostname(OTA_HOSTNAME);
#ifdef OTA_PASSWORD
    ArduinoOTA.setPassword(OTA_PASSWORD);
#endif
    ArduinoOTA.onStart([]() { stopMotors(); });
    ArduinoOTA.begin();

    agentState = WAITING_AGENT;
    Serial.println("[READY] waiting for micro-ROS agent");
}

// ── Loop ──────────────────────────────────────────────────────────────────────
void loop() {
    ArduinoOTA.handle();
    switch (agentState) {
    case WAITING_AGENT:
        stopMotors();
        EXECUTE_EVERY_N_MS(1000,
            agentState = (rmw_uros_ping_agent(300, 1) == RMW_RET_OK) ? AGENT_AVAILABLE : WAITING_AGENT);
        break;

    case AGENT_AVAILABLE:
        if (createEntities()) { agentState = AGENT_CONNECTED; Serial.println("[uROS] CONNECTED"); }
        else                  { destroyEntities(); agentState = WAITING_AGENT; }
        break;

    case AGENT_CONNECTED: {
        // Tolerate transient WiFi: only drop after 3 consecutive missed pings,
        // so a single lost packet doesn't tear down the session (was flapping).
        static uint8_t pingMiss = 0;
        EXECUTE_EVERY_N_MS(2000, {
            if (rmw_uros_ping_agent(300, 1) == RMW_RET_OK) pingMiss = 0;
            else if (++pingMiss >= 3) agentState = AGENT_DISCONNECTED;
        });
        if (agentState == AGENT_CONNECTED)
            rclc_executor_spin_some(&executor, RCL_MS_TO_NS(5));
        break;
    }

    case AGENT_DISCONNECTED:
        stopMotors();
        destroyEntities();
        agentState = WAITING_AGENT;
        Serial.println("[uROS] agent lost — reconnecting");
        break;
    }
    delay(1);
}
