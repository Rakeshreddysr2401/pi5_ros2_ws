// ============================================================
//  Rover Firmware  —  micro-ROS2 on ESP32
//  Transport  : WiFi UDP (micro_ros_agent on Pi5, port 8888)
//  Subscribes : /cmd_vel      geometry_msgs/Twist
//               /servo_angle  std_msgs/UInt16   (angle 0-180)
//  Publishes  : /ir_obstacle  std_msgs/Bool     (true = obstacle)
//
//  L298N wiring:
//    Motor A (LEFT)   IN1=26  IN2=25  ENA=14 (PWM)
//    Motor B (RIGHT)  IN3=33  IN4=32  ENB=27 (PWM)
//    IR sensor        GPIO 34  (LOW = obstacle detected)
//    Servo            GPIO 18
//
//  micro-ROS2 agent on Pi5:
//    ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
// ============================================================

#include <WiFi.h>
#include <ESP32Servo.h>
#include <micro_ros_arduino.h>
#include <rcl/rcl.h>
#include <rcl/error_handling.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>
#include <geometry_msgs/msg/twist.h>
#include <std_msgs/msg/bool.h>
#include <std_msgs/msg/u_int16.h>

// ── WiFi + agent ─────────────────────────────────────────────────────────────
const char* WIFI_SSID  = "YOUR_SSID";       // <-- change
const char* WIFI_PASS  = "YOUR_PASSWORD";   // <-- change
const char* AGENT_IP   = "192.168.1.100";   // <-- Pi5 IP
const uint16_t AGENT_PORT = 8888;

// ── Motor pins ───────────────────────────────────────────────────────────────
#define IN1  26
#define IN2  25
#define ENA  14   // PWM — wire jumper off, connect to this GPIO
#define IN3  33
#define IN4  32
#define ENB  27   // PWM — wire jumper off, connect to this GPIO

// ── Other pins ───────────────────────────────────────────────────────────────
#define IR_PIN    34
#define SERVO_PIN 18

// ── PWM config ───────────────────────────────────────────────────────────────
#define PWM_CH_A    0
#define PWM_CH_B    1
#define PWM_FREQ    1000
#define PWM_RES     8      // 8-bit: 0–255

// Max linear speed (m/s) that maps to full PWM.
// Tune this to match your actual chassis speed.
#define MAX_LINEAR_VEL  0.30f
#define MAX_ANGULAR_VEL 2.0f   // rad/s
#define WHEEL_BASE      0.15f  // metres between left and right wheels

// Safety watchdog — stop if no /cmd_vel in this many ms
#define CMD_TIMEOUT_MS  500

// ── Globals ──────────────────────────────────────────────────────────────────
Servo headServo;
unsigned long lastCmdMs = 0;

rcl_subscription_t cmdVelSub;
rcl_subscription_t servoSub;
rcl_publisher_t    irPub;
rcl_timer_t        irTimer;

geometry_msgs__msg__Twist   twistMsg;
std_msgs__msg__UInt16       servoMsg;
std_msgs__msg__Bool         irMsg;

rclc_executor_t  executor;
rclc_support_t   support;
rcl_allocator_t  allocator;
rcl_node_t       node;

// ── Motor driver ─────────────────────────────────────────────────────────────
// vel: -1.0 (full reverse) … +1.0 (full forward)
void setMotor(int in1, int in2, int pwmCh, float vel) {
    int pwm = (int)(fabsf(vel) * 255.0f);
    if (pwm > 255) pwm = 255;

    if (vel > 0.02f) {
        digitalWrite(in1, HIGH);
        digitalWrite(in2, LOW);
    } else if (vel < -0.02f) {
        digitalWrite(in1, LOW);
        digitalWrite(in2, HIGH);
    } else {
        digitalWrite(in1, LOW);
        digitalWrite(in2, LOW);
        pwm = 0;
    }
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

void servoCb(const void* msgIn) {
    const std_msgs__msg__UInt16* msg = (const std_msgs__msg__UInt16*)msgIn;
    uint16_t angle = msg->data;
    if (angle > 180) angle = 180;
    headServo.write((int)angle);
}

// IR publish timer — 100 ms
void irTimerCb(rcl_timer_t* timer, int64_t /*lastCallTime*/) {
    if (!timer) return;
    irMsg.data = (digitalRead(IR_PIN) == LOW);
    rcl_publish(&irPub, &irMsg, NULL);
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

    // Servo
    headServo.attach(SERVO_PIN);
    headServo.write(90);

    Serial.println("[INIT] Motors, PWM, servo ready");

    // micro-ROS WiFi transport
    Serial.printf("[WiFi] Connecting to agent at %s:%d\n", AGENT_IP, AGENT_PORT);
    set_microros_wifi_transports(
        (char*)WIFI_SSID, (char*)WIFI_PASS,
        (char*)AGENT_IP, AGENT_PORT
    );
    Serial.println("[WiFi] Transport set");

    allocator = rcl_get_default_allocator();
    rclc_support_init(&support, 0, NULL, &allocator);
    rclc_node_init_default(&node, "rover_esp32", "", &support);

    // /cmd_vel subscriber
    rclc_subscription_init_default(
        &cmdVelSub, &node,
        ROSIDL_GET_MSG_TYPE_SUPPORT(geometry_msgs, msg, Twist),
        "/cmd_vel"
    );

    // /servo_angle subscriber
    rclc_subscription_init_default(
        &servoSub, &node,
        ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, UInt16),
        "/servo_angle"
    );

    // /ir_obstacle publisher
    rclc_publisher_init_default(
        &irPub, &node,
        ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Bool),
        "/ir_obstacle"
    );

    // IR timer: 100 ms
    rclc_timer_init_default(&irTimer, &support, RCL_MS_TO_NS(100), irTimerCb);

    // Executor: 2 subs + 1 timer
    rclc_executor_init(&executor, &support.context, 3, &allocator);
    rclc_executor_add_subscription(&executor, &cmdVelSub, &twistMsg, &cmdVelCb, ON_NEW_DATA);
    rclc_executor_add_subscription(&executor, &servoSub,  &servoMsg, &servoCb,  ON_NEW_DATA);
    rclc_executor_add_timer(&executor, &irTimer);

    lastCmdMs = millis();
    Serial.println("[READY] Rover micro-ROS2 ready");
}

// ── Loop ──────────────────────────────────────────────────────────────────────
void loop() {
    // Watchdog: if Pi5 goes silent, stop moving
    if (millis() - lastCmdMs > CMD_TIMEOUT_MS) {
        stopMotors();
    }
    rclc_executor_spin_some(&executor, RCL_MS_TO_NS(10));
    delay(5);
}
