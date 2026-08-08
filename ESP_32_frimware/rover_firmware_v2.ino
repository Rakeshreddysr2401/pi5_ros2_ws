// ============================================================
//  Rover Firmware v2  —  micro-ROS2 on ESP32   (BTS7960 + encoders)
//
//  Hardware : ESP32 DevKit V1, 2x BTS7960 (IBT-2), 4x Rhino GB37 encoder DC
//             motors (12V, 180/193 RPM, 1:30 gear), differential drive
//             (2 motors per side, paralleled onto ONE BTS7960 per side).
//  Transport: WiFi UDP -> micro_ros_agent on Pi5 (192.168.1.16:8888)
//
//  ── ARCHITECTURE (why it's accurate + nav2-ready) ────────────────────────────
//  The PID control loop runs on its OWN FreeRTOS task (core 1, high priority) at a
//  GUARANTEED 50 Hz, using real elapsed time (micros). It is fully decoupled from
//  the micro-ROS/WiFi work in loop() — so even if the network stalls, the wheels
//  are still controlled smoothly at 50 Hz. (Earlier the control ran inside the
//  micro-ROS executor, which stalled it to ~1 Hz -> laggy + a bogus-velocity
//  reversal on long presses. This split fixes both.)
//
//  ── ROS INTERFACE (all BEST_EFFORT QoS — reliable stalls over micro-ROS WiFi) ─
//  IN   /cmd_vel     geometry_msgs/Twist    target body vx, wz   (nav2 / teleop)
//  IN   /pid_gains   geometry_msgs/Vector3  live PID tuning: x=Kp y=Ki z=minMoveDuty
//  OUT  /wheel_state geometry_msgs/Vector3  x=velL y=velR z=cmd vx  (small -> crosses WiFi)
//       For the EKF (cuVSLAM + IMU gyro + wheel odom): a small Pi5 RELAY node
//       subscribes /wheel_state and publishes nav_msgs/Odometry on /wheel_odom
//       (odom->base_link owned by robot_localization, config/ekf.yaml). Odometry
//       is NOT published from here — it exceeds the micro-ROS WiFi message size.
//       This node publishes NO TF.
//
//  ── SAFETY ───────────────────────────────────────────────────────────────────
//  500 ms /cmd_vel watchdog (silence -> stop). Motors driven ONLY while the agent
//  is connected AND a fresh command exists. WiFi agent-reconnect state machine.
//
//  ── TOOLCHAIN (ESP32 Arduino core 3.x + micro_ros_arduino) ──────────────────
//   * core 3.x LEDC is by-PIN: ledcAttach(pin,freq,res) + ledcWrite(pin,duty).
//   * time-sync API varies; behind a compile guard (see createEntities).
//   * EXECUTE_EVERY_N_MS uses Arduino millis().
//   * Encoder internal pull-ups OFF (input-only pads 34/35/36/39 can't have them;
//     GB37 encoders are push-pull @3V3).
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

// Direction flags — SET FROM BENCH TEST (right side mirror-mounted -> inverted).
#define L_MOTOR_DIR (+1)
#define R_MOTOR_DIR (-1)
#define ENC_LF_DIR (+1)
#define ENC_LR_DIR (+1)
#define ENC_RF_DIR (-1)
#define ENC_RR_DIR (-1)
// ╚═════════════════════════════════════════════════════════════════════════════╝

#define WHEEL_CIRC       (float)(M_PI * WHEEL_DIAMETER_M)
#define METRES_PER_COUNT (WHEEL_CIRC / ENCODER_CPR)

// ── Control loop + PID ───────────────────────────────────────────────────────
#define CONTROL_HZ      50
#define CMD_TIMEOUT_MS  500
const float Kff = 1.0f / MAX_WHEEL_VEL;     // feedforward (constant)
// Live-tunable via /pid_gains. Defaults tuned for stable, non-oscillating tracking.
volatile float gKp = 0.6f;
volatile float gKi = 2.0f;
volatile float gMinDuty = 0.08f;            // static-friction breakaway
float integL = 0.0f, integR = 0.0f;         // PID integrators (control task only)

#define DEBUG_SERIAL 1     // 1 = control task prints IN/OUT at ~2 Hz @115200

// ── Shared state (single-core: loop + control task both on core 1 -> aligned
//    32-bit float/uint reads are atomic; volatile is enough, no mutex needed) ──
volatile float    targetVx = 0.0f, targetWz = 0.0f;   // set by /cmd_vel, read by task
volatile uint32_t lastCmdMs = 0;
volatile bool     controlEnabled = false;             // true only when agent connected
volatile float    gVelL = 0.0f, gVelR = 0.0f;         // measured (task -> telemetry)

// ── Odometry pose (integrated in the control task) ───────────────────────────
float odomX = 0.0f, odomY = 0.0f, odomTh = 0.0f;

// ── Encoders ─────────────────────────────────────────────────────────────────
ESP32Encoder encLF, encLR, encRF, encRR;

// ── micro-ROS entities ───────────────────────────────────────────────────────
rcl_subscription_t cmdVelSub;
rcl_subscription_t pidGainsSub;
rcl_publisher_t    wheelStatePub;
geometry_msgs__msg__Twist   twistMsg;
geometry_msgs__msg__Vector3 pidGainsMsg;
geometry_msgs__msg__Vector3 wheelStateMsg;
rclc_executor_t executor;
rclc_support_t  support;
rcl_allocator_t allocator;
rcl_node_t      node;
bool timeSynced = false;

// ── Agent connection state machine ──────────────────────────────────────────
enum AgentState { WAITING_AGENT, AGENT_AVAILABLE, AGENT_CONNECTED, AGENT_DISCONNECTED };
AgentState agentState = WAITING_AGENT;

#define EXECUTE_EVERY_N_MS(MS, X) do {         \
    static uint32_t last = 0;                  \
    uint32_t now = millis();                   \
    if ((now - last) >= (MS)) { last = now; X; } \
} while (0)

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
float pidStep(float target, float meas, float &integ, float dt) {
    if (fabsf(target) < 0.01f) { integ = 0.0f; return 0.0f; }
    float err = target - meas;
    integ += err * dt;
    float ilim = 1.0f / gKi;                 // anti-windup: |Ki*integ| <= 1
    if (integ >  ilim) integ =  ilim;
    if (integ < -ilim) integ = -ilim;
    float out = Kff * target + gKp * err + gKi * integ;
    out += (target > 0.0f) ? gMinDuty : -gMinDuty;
    if (out >  1.0f) out =  1.0f;
    if (out < -1.0f) out = -1.0f;
    return out;
}

// ── Control task — GUARANTEED 50 Hz, independent of micro-ROS/WiFi ───────────
void controlTask(void* /*arg*/) {
    long lLF = (long)encLF.getCount() * ENC_LF_DIR;
    long lLR = (long)encLR.getCount() * ENC_LR_DIR;
    long lRF = (long)encRF.getCount() * ENC_RF_DIR;
    long lRR = (long)encRR.getCount() * ENC_RR_DIR;
    uint32_t lastUs = micros();
    TickType_t wake = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(1000 / CONTROL_HZ);   // 20 ms
    uint16_t dbg = 0;

    for (;;) {
        vTaskDelayUntil(&wake, period);

        uint32_t nowUs = micros();
        float dt = (nowUs - lastUs) * 1e-6f;   // real elapsed (unsigned rollover-safe)
        lastUs = nowUs;
        if (dt <= 0.0f) continue;

        // measure per-side velocity (average both encoders on each side)
        long cLF = (long)encLF.getCount() * ENC_LF_DIR;
        long cLR = (long)encLR.getCount() * ENC_LR_DIR;
        long cRF = (long)encRF.getCount() * ENC_RF_DIR;
        long cRR = (long)encRR.getCount() * ENC_RR_DIR;
        long dLF = cLF - lLF; lLF = cLF;
        long dLR = cLR - lLR; lLR = cLR;
        long dRF = cRF - lRF; lRF = cRF;
        long dRR = cRR - lRR; lRR = cRR;
        float distL = 0.5f * (dLF + dLR) * METRES_PER_COUNT;
        float distR = 0.5f * (dRF + dRR) * METRES_PER_COUNT;
        float velL = distL / dt;
        float velR = distR / dt;
        gVelL = velL; gVelR = velR;

        // target with safety gates: only drive when connected AND command is fresh
        float tvx = targetVx, twz = targetWz;
        if (!controlEnabled || (millis() - lastCmdMs > CMD_TIMEOUT_MS)) { tvx = 0.0f; twz = 0.0f; }

        float wL = tvx - twz * WHEEL_BASE_M * 0.5f;
        float wR = tvx + twz * WHEEL_BASE_M * 0.5f;
        driveSide(L_RPWM, L_LPWM, L_MOTOR_DIR, pidStep(wL, velL, integL, dt));
        driveSide(R_RPWM, R_LPWM, R_MOTOR_DIR, pidStep(wR, velR, integR, dt));

        // odometry pose (for the Pi5 relay / debug)
        float ds  = 0.5f * (distL + distR);
        float dth = (distR - distL) / WHEEL_BASE_M;
        odomX  += ds * cosf(odomTh + 0.5f * dth);
        odomY  += ds * sinf(odomTh + 0.5f * dth);
        odomTh += dth;

#if DEBUG_SERIAL
        if (++dbg >= (CONTROL_HZ / 2)) {   // ~2 Hz -> confirms the loop really runs at 50 Hz
            dbg = 0;
            Serial.printf("IN vx=%.2f wz=%.2f | OUT velL=%.2f velR=%.2f | odom(x=%.2f y=%.2f th=%.2f) dt=%.3f\n",
                          tvx, twz, velL, velR, odomX, odomY, odomTh, dt);
        }
#endif
    }
}

// ── Callbacks (run in loop()/executor context) ───────────────────────────────
void cmdVelCb(const void* msgIn) {
    const geometry_msgs__msg__Twist* m = (const geometry_msgs__msg__Twist*)msgIn;
    targetVx = (float)m->linear.x;
    targetWz = (float)m->angular.z;
    lastCmdMs = millis();
}

void pidGainsCb(const void* msgIn) {   // live tuning: Vector3 x=Kp y=Ki z=minMoveDuty
    const geometry_msgs__msg__Vector3* m = (const geometry_msgs__msg__Vector3*)msgIn;
    gKp = (float)m->x;
    gKi = (float)m->y;
    gMinDuty = (float)m->z;
    integL = integR = 0.0f;
    Serial.printf("[PID] set Kp=%.3f Ki=%.3f minDuty=%.3f\n", gKp, gKi, gMinDuty);
}

// ── micro-ROS entity lifecycle ───────────────────────────────────────────────
bool createEntities() {
    allocator = rcl_get_default_allocator();
    if (rclc_support_init(&support, 0, NULL, &allocator) != RCL_RET_OK) return false;
    rmw_context_t* rmw_context = rcl_context_get_rmw_context(&support.context);
    (void) rmw_uros_set_context_entity_destroy_session_timeout(rmw_context, 0);

    if (rclc_node_init_default(&node, "rover_esp32", "", &support) != RCL_RET_OK) return false;

    if (rclc_subscription_init_best_effort(&cmdVelSub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(geometry_msgs, msg, Twist), "/cmd_vel") != RCL_RET_OK) return false;
    if (rclc_subscription_init_best_effort(&pidGainsSub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(geometry_msgs, msg, Vector3), "/pid_gains") != RCL_RET_OK) return false;
    if (rclc_publisher_init_best_effort(&wheelStatePub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(geometry_msgs, msg, Vector3), "/wheel_state") != RCL_RET_OK) return false;

    rclc_executor_init(&executor, &support.context, 2, &allocator);   // 2 subs
    rclc_executor_add_subscription(&executor, &cmdVelSub, &twistMsg, &cmdVelCb, ON_NEW_DATA);
    rclc_executor_add_subscription(&executor, &pidGainsSub, &pidGainsMsg, &pidGainsCb, ON_NEW_DATA);

#if defined(RMW_UROS_SYNC_SESSION) || __has_include(<rmw_microros/time_sync.h>)
    timeSynced = (rmw_uros_sync_session(1000) == RMW_RET_OK);
#else
    timeSynced = false;
#endif
    lastCmdMs = millis();
    Serial.printf("[uROS] entities live (time sync %s) — IN /cmd_vel /pid_gains, OUT /wheel_state\n",
                  timeSynced ? "OK" : "off");
    return true;
}

void destroyEntities() {
    rcl_subscription_fini(&cmdVelSub, &node);
    rcl_subscription_fini(&pidGainsSub, &node);
    rcl_publisher_fini(&wheelStatePub, &node);
    rclc_executor_fini(&executor);
    rcl_node_fini(&node);
    rclc_support_fini(&support);
    Serial.println("[uROS] entities destroyed");
}

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("=== Rover ESP32 v2 (BTS7960 + encoders, RT control task) ===");

    // BTS7960 enables HIGH
    pinMode(L_REN, OUTPUT); pinMode(L_LEN, OUTPUT);
    pinMode(R_REN, OUTPUT); pinMode(R_LEN, OUTPUT);
    digitalWrite(L_REN, HIGH); digitalWrite(L_LEN, HIGH);
    digitalWrite(R_REN, HIGH); digitalWrite(R_LEN, HIGH);

    // PWM (core 3.x)
    bool pwmOK = true;
    pwmOK &= ledcAttach(L_RPWM, PWM_FREQ, PWM_RES);
    pwmOK &= ledcAttach(L_LPWM, PWM_FREQ, PWM_RES);
    pwmOK &= ledcAttach(R_RPWM, PWM_FREQ, PWM_RES);
    pwmOK &= ledcAttach(R_LPWM, PWM_FREQ, PWM_RES);
    Serial.printf("[PWM] attach %s\n", pwmOK ? "OK" : "FAILED — motors won't drive");
    stopMotors();

    // Encoders (PCNT hardware quadrature). Pull-ups OFF (push-pull @3V3).
    ESP32Encoder::useInternalWeakPullResistors = puType::none;
    encLF.attachFullQuad(ENC_LF_A, ENC_LF_B);
    encLR.attachFullQuad(ENC_LR_A, ENC_LR_B);
    encRF.attachFullQuad(ENC_RF_A, ENC_RF_B);
    encRR.attachFullQuad(ENC_RR_A, ENC_RR_B);
    encLF.clearCount(); encLR.clearCount();
    encRF.clearCount(); encRR.clearCount();
    Serial.println("[ENC] 4x quadrature attached");

    // Real-time control task on core 1, priority above the Arduino loop so it
    // preempts any micro-ROS/WiFi stall and holds a solid 50 Hz.
    xTaskCreatePinnedToCore(controlTask, "control", 8192, NULL, 2, NULL, 1);
    Serial.println("[CTRL] 50 Hz control task started (core 1)");

    Serial.printf("[WiFi] agent %s:%d\n", AGENT_IP, AGENT_PORT);
    set_microros_wifi_transports((char*)WIFI_SSID, (char*)WIFI_PASS, (char*)AGENT_IP, AGENT_PORT);
    WiFi.setSleep(false);

    ArduinoOTA.setHostname(OTA_HOSTNAME);
#ifdef OTA_PASSWORD
    ArduinoOTA.setPassword(OTA_PASSWORD);
#endif
    ArduinoOTA.onStart([]() { controlEnabled = false; stopMotors(); });
    ArduinoOTA.begin();

    agentState = WAITING_AGENT;
    Serial.println("[READY] waiting for micro-ROS agent");
}

// ── Loop — micro-ROS messaging only (control lives in the task) ──────────────
void loop() {
    ArduinoOTA.handle();
    switch (agentState) {
    case WAITING_AGENT:
        controlEnabled = false;   // task holds motors stopped
        EXECUTE_EVERY_N_MS(1000,
            agentState = (rmw_uros_ping_agent(300, 1) == RMW_RET_OK) ? AGENT_AVAILABLE : WAITING_AGENT);
        break;

    case AGENT_AVAILABLE:
        if (createEntities()) { agentState = AGENT_CONNECTED; controlEnabled = true; Serial.println("[uROS] CONNECTED"); }
        else                  { destroyEntities(); agentState = WAITING_AGENT; }
        break;

    case AGENT_CONNECTED: {
        static uint8_t pingMiss = 0;
        EXECUTE_EVERY_N_MS(2000, {
            if (rmw_uros_ping_agent(300, 1) == RMW_RET_OK) pingMiss = 0;
            else if (++pingMiss >= 3) agentState = AGENT_DISCONNECTED;
        });
        if (agentState == AGENT_CONNECTED) {
            rclc_executor_spin_some(&executor, RCL_MS_TO_NS(5));
            // publish telemetry from the shared measured velocities (~20 Hz)
            EXECUTE_EVERY_N_MS(50, {
                wheelStateMsg.x = gVelL; wheelStateMsg.y = gVelR; wheelStateMsg.z = targetVx;
                rcl_publish(&wheelStatePub, &wheelStateMsg, NULL);
            });
        }
        break;
    }

    case AGENT_DISCONNECTED:
        controlEnabled = false;
        destroyEntities();
        agentState = WAITING_AGENT;
        Serial.println("[uROS] agent lost — reconnecting");
        break;
    }
    delay(1);
}
