// ============================================================
//  Rover Firmware  —  micro-ROS2 on ESP32
//  Transport  : WiFi UDP (micro_ros_agent on Pi5, port 8888)
//  Subscribes : /cmd_vel      geometry_msgs/Twist
//               /servo_pan    std_msgs/UInt16   (0-180, 90 = camera forward)
//               /servo_tilt   std_msgs/UInt16   (0-180, 90 = camera level)
//  Publishes  : /ir_obstacle  std_msgs/Bool     (true = obstacle)
//
//  L298N wiring:
//    Motor A (LEFT)   IN1=26  IN2=25  ENA=14 (PWM)
//    Motor B (RIGHT)  IN3=33  IN4=32  ENB=27 (PWM)
//    IR sensor        GPIO 34  (LOW = obstacle detected)
//    Pan servo        GPIO 18  (camera head, horizontal)
//    Tilt servo       GPIO 19  (camera head, vertical)
//
//  micro-ROS2 agent on Pi5:
//    ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
//
//  Reconnection: state machine pings the agent forever. Agent restart /
//  Pi5 reboot / WiFi drop all recover without power-cycling the rover.
//
//  OTA: after first USB flash, reflash over WiFi:
//    arduino-cli upload -p rover-esp32.local --fqbn esp32:esp32:esp32 ...
// ============================================================

#include <WiFi.h>
#include <ArduinoOTA.h>
#include <ESP32Servo.h>
#include <micro_ros_arduino.h>
#include <rcl/rcl.h>
#include <rcl/error_handling.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>
#include <rmw_microros/rmw_microros.h>
#include <geometry_msgs/msg/twist.h>
#include <std_msgs/msg/bool.h>
#include <std_msgs/msg/u_int16.h>

// ── WiFi + agent ─────────────────────────────────────────────────────────────
const char* WIFI_SSID  = "Airtel_Singireddy's";      // home AP (Jetson+Pi5 verified on it)
const char* WIFI_PASS  = "YOUR_PASSWORD";              // <-- fill in locally, NEVER commit (public repo)
const char* AGENT_IP   = "192.168.1.16";               // Pi5 wlan0 — reserve this IP in the router
const uint16_t AGENT_PORT = 8888;
const char* OTA_HOSTNAME = "rover-esp32";
const char* OTA_PASS     = "YOUR_OTA_PASSWORD";        // <-- fill in locally, NEVER commit

// ── Motor pins ───────────────────────────────────────────────────────────────
#define IN1  26
#define IN2  25
#define ENA  14   // PWM — wire jumper off, connect to this GPIO
#define IN3  33
#define IN4  32
#define ENB  27   // PWM — wire jumper off, connect to this GPIO

// ── Other pins ───────────────────────────────────────────────────────────────
#define IR_PIN    34
#define SERVO_PAN_PIN  18
#define SERVO_TILT_PIN 19

// ── PWM config ───────────────────────────────────────────────────────────────
#define PWM_CH_A    0
#define PWM_CH_B    1
#define PWM_FREQ    1000
#define PWM_RES     8      // 8-bit: 0–255

// Motors stall (hum, no motion) below ~50% duty on this L298N/gearbox.
// Remap so any nonzero command lands in [PWM_MIN, 255] — replaces the
// cmd_vel_deadband.py shim on the Jetson.
#define PWM_MIN     130

// Max linear speed (m/s) that maps to full PWM.
// Tune this to match your actual chassis speed.
#define MAX_LINEAR_VEL  0.30f
#define MAX_ANGULAR_VEL 2.0f   // rad/s
#define WHEEL_BASE      0.15f  // metres between left and right wheels

// Safety watchdog — stop if no /cmd_vel in this many ms
#define CMD_TIMEOUT_MS  500

// ── Agent connection state machine ──────────────────────────────────────────
enum AgentState {
    WAITING_AGENT,
    AGENT_AVAILABLE,
    AGENT_CONNECTED,
    AGENT_DISCONNECTED
};
AgentState agentState = WAITING_AGENT;

#define EXECUTE_EVERY_N_MS(MS, X)  do {            \
    static volatile int64_t init = -1;             \
    if (init == -1) { init = uxr_millis(); }       \
    if (uxr_millis() - init > MS) {                \
        X;                                         \
        init = uxr_millis();                       \
    }                                              \
} while (0)

// ── Globals ──────────────────────────────────────────────────────────────────
Servo panServo;    // camera head pan  (90 = forward)
Servo tiltServo;   // camera head tilt (90 = level; mechanical range 60-120)
unsigned long lastCmdMs = 0;

rcl_subscription_t cmdVelSub;
rcl_subscription_t servoPanSub;
rcl_subscription_t servoTiltSub;
rcl_publisher_t    irPub;
rcl_timer_t        irTimer;

geometry_msgs__msg__Twist   twistMsg;
std_msgs__msg__UInt16       servoPanMsg;
std_msgs__msg__UInt16       servoTiltMsg;
std_msgs__msg__Bool         irMsg;

rclc_executor_t  executor;
rclc_support_t   support;
rcl_allocator_t  allocator;
rcl_node_t       node;

// ── Motor driver ─────────────────────────────────────────────────────────────
// vel: -1.0 (full reverse) … +1.0 (full forward)
void setMotor(int in1, int in2, int pwmCh, float vel) {
    int pwm;
    if (vel > 0.02f) {
        digitalWrite(in1, HIGH);
        digitalWrite(in2, LOW);
        pwm = (int)(PWM_MIN + fabsf(vel) * (255.0f - PWM_MIN));
    } else if (vel < -0.02f) {
        digitalWrite(in1, LOW);
        digitalWrite(in2, HIGH);
        pwm = (int)(PWM_MIN + fabsf(vel) * (255.0f - PWM_MIN));
    } else {
        digitalWrite(in1, LOW);
        digitalWrite(in2, LOW);
        pwm = 0;
    }
    if (pwm > 255) pwm = 255;
    ledcWrite(pwmCh, pwm);
}

void stopMotors() {
    setMotor(IN1, IN2, PWM_CH_A, 0);
    setMotor(IN3, IN4, PWM_CH_B, 0);
}

// Differential drive: Twist → left/right wheel velocities
void driveFromTwist(float linearX, float angularZ) {
    // Normalize linear and angular components independently to utilize full PWM range
    float linear_pct  = linearX / MAX_LINEAR_VEL;
    float angular_pct = angularZ / MAX_ANGULAR_VEL;

    float left  = linear_pct - angular_pct;
    float right = linear_pct + angular_pct;

    // Normalise to -1..1
    if (left  >  1.0f) left  =  1.0f;
    if (left  < -1.0f) left  = -1.0f;
    if (right >  1.0f) right =  1.0f;
    if (right < -1.0f) right = -1.0f;

    setMotor(IN1, IN2, PWM_CH_A, left);
    setMotor(IN3, IN4, PWM_CH_B, right);
}

// ── Callbacks ─────────────────────────────────────────────────────────────────
void cmdVelCb(const void* msgIn) {
    const geometry_msgs__msg__Twist* msg =
        (const geometry_msgs__msg__Twist*)msgIn;

    lastCmdMs = millis();

    // IR not wired — skip obstacle check until sensor is connected
    // bool obstacle = (digitalRead(IR_PIN) == LOW);
    // if (obstacle && msg->linear.x > 0.0f) { stopMotors(); return; }

    driveFromTwist((float)msg->linear.x, (float)msg->angular.z);
}

void servoPanCb(const void* msgIn) {
    const std_msgs__msg__UInt16* msg = (const std_msgs__msg__UInt16*)msgIn;
    uint16_t angle = msg->data;
    if (angle > 180) angle = 180;
    panServo.write((int)angle);
}

void servoTiltCb(const void* msgIn) {
    const std_msgs__msg__UInt16* msg = (const std_msgs__msg__UInt16*)msgIn;
    uint16_t angle = msg->data;
    // Tilt is mechanically limited (camera cable + mount): clamp 60-120
    // (= -30..+30 deg from level) no matter what the brain sends.
    if (angle < 60)  angle = 60;
    if (angle > 120) angle = 120;
    tiltServo.write((int)angle);
}

// IR publish timer — 100 ms
void irTimerCb(rcl_timer_t* timer, int64_t /*lastCallTime*/) {
    if (!timer) return;
    irMsg.data = (digitalRead(IR_PIN) == LOW);
    rcl_publish(&irPub, &irMsg, NULL);
}

// ── micro-ROS entity lifecycle ───────────────────────────────────────────────
bool createEntities() {
    allocator = rcl_get_default_allocator();
    if (rclc_support_init(&support, 0, NULL, &allocator) != RCL_RET_OK) return false;

    // Fast session teardown so destroyEntities() doesn't block when agent is gone
    rmw_context_t* rmw_context = rcl_context_get_rmw_context(&support.context);
    (void) rmw_uros_set_context_entity_destroy_session_timeout(rmw_context, 0);

    if (rclc_node_init_default(&node, "rover_esp32", "", &support) != RCL_RET_OK) return false;

    if (rclc_subscription_init_default(
            &cmdVelSub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(geometry_msgs, msg, Twist),
            "/cmd_vel") != RCL_RET_OK) return false;

    if (rclc_subscription_init_default(
            &servoPanSub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, UInt16),
            "/servo_pan") != RCL_RET_OK) return false;

    if (rclc_subscription_init_default(
            &servoTiltSub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, UInt16),
            "/servo_tilt") != RCL_RET_OK) return false;

    if (rclc_publisher_init_default(
            &irPub, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Bool),
            "/ir_obstacle") != RCL_RET_OK) return false;

    if (rclc_timer_init_default(&irTimer, &support, RCL_MS_TO_NS(100), irTimerCb) != RCL_RET_OK) return false;

    rclc_executor_init(&executor, &support.context, 4, &allocator);
    rclc_executor_add_subscription(&executor, &cmdVelSub,    &twistMsg,     &cmdVelCb,    ON_NEW_DATA);
    rclc_executor_add_subscription(&executor, &servoPanSub,  &servoPanMsg,  &servoPanCb,  ON_NEW_DATA);
    rclc_executor_add_subscription(&executor, &servoTiltSub, &servoTiltMsg, &servoTiltCb, ON_NEW_DATA);
    rclc_executor_add_timer(&executor, &irTimer);

    lastCmdMs = millis();
    Serial.println("[uROS] Entities created — session live");
    return true;
}

void destroyEntities() {
    rcl_subscription_fini(&cmdVelSub, &node);
    rcl_subscription_fini(&servoPanSub, &node);
    rcl_subscription_fini(&servoTiltSub, &node);
    rcl_publisher_fini(&irPub, &node);
    rcl_timer_fini(&irTimer);
    rclc_executor_fini(&executor);
    rcl_node_fini(&node);
    rclc_support_fini(&support);
    Serial.println("[uROS] Entities destroyed");
}

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("=== Rover ESP32 starting ===");

    // Motor GPIO
    pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
    pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);
    pinMode(IR_PIN, INPUT);
    stopMotors();

    // PWM channels
    ledcSetup(PWM_CH_A, PWM_FREQ, PWM_RES);
    ledcSetup(PWM_CH_B, PWM_FREQ, PWM_RES);
    ledcAttachPin(ENA, PWM_CH_A);
    ledcAttachPin(ENB, PWM_CH_B);

    // Camera head servos — centre on boot so the D555 faces forward/level
    panServo.attach(SERVO_PAN_PIN);
    tiltServo.attach(SERVO_TILT_PIN);
    panServo.write(90);
    tiltServo.write(90);

    Serial.println("[INIT] Motors, PWM, pan/tilt servos ready");

    // micro-ROS WiFi transport (blocks until WiFi associates)
    Serial.printf("[WiFi] Connecting to agent at %s:%d\n", AGENT_IP, AGENT_PORT);
    set_microros_wifi_transports(
        (char*)WIFI_SSID, (char*)WIFI_PASS,
        (char*)AGENT_IP, AGENT_PORT
    );
    // Modem sleep adds ~100ms latency to every /cmd_vel — keep the radio awake
    WiFi.setSleep(false);
    Serial.println("[WiFi] Transport set, modem sleep off");

    ArduinoOTA.setHostname(OTA_HOSTNAME);
    ArduinoOTA.setPassword(OTA_PASS);
    ArduinoOTA.onStart([]() { stopMotors(); });
    ArduinoOTA.begin();

    agentState = WAITING_AGENT;
    Serial.println("[READY] Waiting for micro-ROS agent");
}

// ── Loop ──────────────────────────────────────────────────────────────────────
void loop() {
    ArduinoOTA.handle();

    switch (agentState) {
    case WAITING_AGENT:
        stopMotors();
        // Home WiFi RTT to the Pi5 can spike >150ms — use generous ping timeouts
        EXECUTE_EVERY_N_MS(1000,
            agentState = (rmw_uros_ping_agent(500, 2) == RMW_RET_OK)
                             ? AGENT_AVAILABLE : WAITING_AGENT);
        break;

    case AGENT_AVAILABLE:
        if (createEntities()) {
            agentState = AGENT_CONNECTED;
        } else {
            destroyEntities();
            agentState = WAITING_AGENT;
        }
        break;

    case AGENT_CONNECTED:
        EXECUTE_EVERY_N_MS(2000,
            agentState = (rmw_uros_ping_agent(500, 3) == RMW_RET_OK)
                             ? AGENT_CONNECTED : AGENT_DISCONNECTED);
        if (agentState == AGENT_CONNECTED) {
            // Watchdog: if Pi5 goes silent, stop moving
            if (millis() - lastCmdMs > CMD_TIMEOUT_MS) {
                stopMotors();
            }
            rclc_executor_spin_some(&executor, RCL_MS_TO_NS(10));
        }
        break;

    case AGENT_DISCONNECTED:
        stopMotors();
        destroyEntities();
        agentState = WAITING_AGENT;
        Serial.println("[uROS] Agent lost — reconnecting");
        break;
    }

    delay(2);
}
