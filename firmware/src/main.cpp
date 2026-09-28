#include <Arduino.h>
#if defined(WOKWI_UART_PROBE)
#include <string.h>

static constexpr uint8_t PROBE_LED_PIN = 13;
static char probeCommand[64];
static size_t probeCommandLength = 0;
static unsigned long lastProbeHeartbeat = 0;
static bool probeLedOn = false;

void setup() {
    pinMode(PROBE_LED_PIN, OUTPUT);
    for (int pulse = 0; pulse < 4; ++pulse) {
        digitalWrite(PROBE_LED_PIN, HIGH);
        delay(150);
        digitalWrite(PROBE_LED_PIN, LOW);
        delay(150);
    }
    Serial.begin(115200);
    Serial.println("UART_PROBE: BOOT");
}

void loop() {
    while (Serial.available() > 0) {
        const char value = static_cast<char>(Serial.read());
        if (value == '\n' || value == '\r') {
            if (probeCommandLength > 0) {
                probeCommand[probeCommandLength] = '\0';
                if (strcmp(probeCommand, "STOP") == 0) {
                    Serial.println("DONE: STOP");
                } else {
                    Serial.printf("UART_PROBE: RX=%s\n", probeCommand);
                }
                probeCommandLength = 0;
            }
        } else if (probeCommandLength + 1 < sizeof(probeCommand)) {
            probeCommand[probeCommandLength++] = value;
        } else {
            probeCommandLength = 0;
        }
    }

    if (millis() - lastProbeHeartbeat >= 1000) {
        lastProbeHeartbeat = millis();
        probeLedOn = !probeLedOn;
        digitalWrite(PROBE_LED_PIN, probeLedOn ? HIGH : LOW);
        Serial.println("UART_PROBE: HEARTBEAT");
    }
}

#else
#include <AccelStepper.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

// Pin definitions
static constexpr uint8_t PIN_PM_STEP = 4;
static constexpr uint8_t PIN_PM_DIR = 5;
static constexpr uint8_t PIN_RM_STEP = 6;
static constexpr uint8_t PIN_RM_DIR = 7;
static constexpr uint8_t PIN_LM_STEP = 10;
static constexpr uint8_t PIN_LM_DIR = 11;
static constexpr uint8_t PIN_GATE_SERVO = 14;
static constexpr uint8_t PIN_L1_PM_HOME = 15;
static constexpr uint8_t PIN_L2_LM_HOME = 16;
static constexpr uint8_t PIN_L3_RM_HOME = 17;
static constexpr uint8_t PIN_RUN_LED = 18;
static constexpr uint8_t PIN_HOME_LED = 12;
static constexpr uint8_t PIN_IDLE_LED = 13;

enum class OperationState : uint8_t { IDLE, RUN, HOME };

void setOperationState(OperationState state) {
    digitalWrite(PIN_RUN_LED, state == OperationState::RUN ? HIGH : LOW);
    digitalWrite(PIN_HOME_LED, state == OperationState::HOME ? HIGH : LOW);
    digitalWrite(PIN_IDLE_LED, state == OperationState::IDLE ? HIGH : LOW);
}

// Internal driver limits; physical-unit conversion is centralized in Python.
static constexpr float MAX_SPEED_STEPS_S = 500.0f;
static constexpr float ACCELERATION_STEPS_S2 = 1000.0f;
static constexpr int GATE_OPEN_DEG = 0;
static constexpr int GATE_CLOSED_DEG = 180;

// Gate servo driven directly via the core's LEDC PWM API (ESP32Servo's attach()
// hangs on this core/board combination).
static constexpr uint8_t GATE_PWM_CHANNEL = 0;
static constexpr uint32_t GATE_PWM_FREQ_HZ = 50;
static constexpr uint8_t GATE_PWM_RESOLUTION_BITS = 14;
static constexpr uint32_t GATE_PWM_MAX_DUTY = (1u << GATE_PWM_RESOLUTION_BITS) - 1;
static constexpr uint32_t GATE_MIN_PULSE_US = 500;
static constexpr uint32_t GATE_MAX_PULSE_US = 2500;
static constexpr uint32_t GATE_PERIOD_US = 1000000UL / GATE_PWM_FREQ_HZ;

AccelStepper stepperPM(AccelStepper::DRIVER, PIN_PM_STEP, PIN_PM_DIR);
AccelStepper stepperRM(AccelStepper::DRIVER, PIN_RM_STEP, PIN_RM_DIR);
AccelStepper stepperLM(AccelStepper::DRIVER, PIN_LM_STEP, PIN_LM_DIR);

void setGateAngle(int angleDegrees) {
    angleDegrees = constrain(angleDegrees, 0, 180);
    const uint32_t pulseUs = GATE_MIN_PULSE_US +
        (GATE_MAX_PULSE_US - GATE_MIN_PULSE_US) * static_cast<uint32_t>(angleDegrees) / 180;
    const uint32_t duty = static_cast<uint32_t>(
        static_cast<uint64_t>(pulseUs) * GATE_PWM_MAX_DUTY / GATE_PERIOD_US);
    ledcWrite(GATE_PWM_CHANNEL, duty);
}

struct Axis {
    const char* name;
    AccelStepper* stepper;
    uint8_t homePin;
    bool homed;
};

Axis axes[] = {
    {"PM", &stepperPM, PIN_L1_PM_HOME, false},
    {"RM", &stepperRM, PIN_L3_RM_HOME, false},
    {"LM", &stepperLM, PIN_L2_LM_HOME, false},
};

static constexpr size_t AXIS_COUNT = sizeof(axes) / sizeof(axes[0]);
enum class MotionKind : uint8_t { IDLE, TARGET, HOME_SEEK };
enum class MotionCommand : uint8_t { MOVE, HOME_APPROACH, HOME };

MotionKind motionKind = MotionKind::IDLE;
MotionCommand motionCommand = MotionCommand::MOVE;
Axis* activeAxis = nullptr;
char commandBuffer[96];
size_t commandLength = 0;
bool commandOverflow = false;

Axis* findAxis(const char* name) {
    for (Axis& axis : axes) {
        if (strcmp(axis.name, name) == 0) return &axis;
    }
    return nullptr;
}

bool homeSwitchTriggered(const Axis& axis) {
#if defined(WOKWI_FORCE_HOME_SWITCHES)
    // No physical switch in simulation; the lead screw reaching 0 is the real trigger condition.
    return axis.stepper->currentPosition() <= 0;
#else
    return digitalRead(axis.homePin) == LOW;
#endif
}

void reportError(const char* message) {
    Serial.printf("ERROR: %s\n", message);
}

bool motionIdle() {
    return motionKind == MotionKind::IDLE;
}

void configureStepper(AccelStepper& stepper) {
    stepper.setMaxSpeed(MAX_SPEED_STEPS_S);
    stepper.setAcceleration(ACCELERATION_STEPS_S2);
}

void stopPulses(AccelStepper& stepper) {
    stepper.setCurrentPosition(stepper.currentPosition());
}

void completeMotion() {
    if (motionCommand == MotionCommand::MOVE) {
        Serial.printf("DONE: MOVE %s\n", activeAxis->name);
    } else if (motionCommand == MotionCommand::HOME_APPROACH) {
        Serial.printf("DONE: HOME_APPROACH %s\n", activeAxis->name);
    } else {
        Serial.printf("DONE: HOME %s\n", activeAxis->name);
    }
    motionKind = MotionKind::IDLE;
    activeAxis = nullptr;
}

bool beginTargetMove(Axis& axis, long targetSteps, float speedStepsS, MotionCommand command) {
    if (!motionIdle()) {
        reportError("BUSY");
        return false;
    }
    if (!isfinite(speedStepsS) || speedStepsS <= 0.0f || speedStepsS > MAX_SPEED_STEPS_S) {
        reportError("INVALID_MOVE_PARAMETERS");
        return false;
    }
    if (command == MotionCommand::MOVE && !axis.homed) {
        reportError("HOME_REQUIRED");
        return false;
    }

    motionKind = MotionKind::TARGET;
    motionCommand = command;
    activeAxis = &axis;
    axis.stepper->setMaxSpeed(speedStepsS);
    axis.stepper->moveTo(targetSteps);
    if (!axis.stepper->isRunning()) completeMotion();
    return true;
}

void beginHomeSeek(Axis& axis, float speedStepsS, long safeguardSteps) {
    if (!motionIdle()) {
        reportError("BUSY");
        return;
    }
    if (!isfinite(speedStepsS) || speedStepsS <= 0.0f || speedStepsS > MAX_SPEED_STEPS_S ||
        safeguardSteps >= 0) {
        reportError("INVALID_HOME_PARAMETERS");
        return;
    }

    motionCommand = MotionCommand::HOME;
    activeAxis = &axis;
    if (homeSwitchTriggered(axis)) {
        stopPulses(*axis.stepper);
        axis.stepper->setCurrentPosition(0);
        axis.homed = true;
        motionKind = MotionKind::IDLE;
        Serial.printf("DONE: HOME %s\n", axis.name);
        activeAxis = nullptr;
        return;
    }

    motionKind = MotionKind::HOME_SEEK;
    axis.stepper->setMaxSpeed(speedStepsS);
    axis.stepper->moveTo(safeguardSteps);
}

void haltImmediately() {
    const bool wasMoving = !motionIdle();
    for (Axis& axis : axes) {
        stopPulses(*axis.stepper);
        axis.homed = false;
    }
    motionKind = MotionKind::IDLE;
    activeAxis = nullptr;
    setOperationState(OperationState::IDLE);
    if (wasMoving) reportError("MOTION_STOPPED");
    Serial.println("DONE: STOP");
}

void handleCommand(const char* command) {
    char verb[20] = {};
    char axisName[8] = {};
    float firstValue = 0.0f;
    float secondValue = 0.0f;
    const int fields = sscanf(command, "%19s %7s %f %f", verb, axisName, &firstValue, &secondValue);

    if (strcmp(verb, "STOP") == 0) {
        haltImmediately();
        return;
    }
    if (strcmp(verb, "GATE") == 0) {
        float angle = 0.0f;
        if (sscanf(command, "%19s %f", verb, &angle) != 2 || angle < 0 || angle > 180) {
            reportError("INVALID_GATE_ANGLE");
            return;
        }
        setGateAngle(static_cast<int>(lroundf(angle)));
        Serial.println("DONE: GATE");
        return;
    }
    if (strcmp(verb, "STATE") == 0) {
        char state[8] = {};
        if (sscanf(command, "%19s %7s", verb, state) != 2 ||
            (strcmp(state, "IDLE") != 0 && strcmp(state, "RUN") != 0 && strcmp(state, "HOME") != 0)) {
            reportError("INVALID_OPERATION_STATE");
            return;
        }
        setOperationState(strcmp(state, "RUN") == 0 ? OperationState::RUN :
            strcmp(state, "HOME") == 0 ? OperationState::HOME : OperationState::IDLE);
        Serial.println("DONE: STATE");
        return;
    }

    Axis* axis = findAxis(axisName);
    if (axis == nullptr) {
        reportError("UNKNOWN_AXIS");
        return;
    }

    if (strcmp(verb, "MOVE") == 0) {
        if (fields != 4) {
            reportError("INVALID_MOVE_COMMAND");
            return;
        }
        if (beginTargetMove(*axis, lroundf(firstValue), secondValue, MotionCommand::MOVE)) {
            Serial.printf("OK: MOVE %s\n", axis->name);
        }
    } else if (strcmp(verb, "HOME_APPROACH") == 0) {
        if (fields != 4) {
            reportError("INVALID_HOME_APPROACH_COMMAND");
            return;
        }
        if (beginTargetMove(*axis, lroundf(firstValue), secondValue, MotionCommand::HOME_APPROACH)) {
            Serial.printf("OK: HOME_APPROACH %s\n", axis->name);
        }
    } else if (strcmp(verb, "HOME") == 0) {
        if (fields != 4) {
            reportError("INVALID_HOME_COMMAND");
            return;
        }
        beginHomeSeek(*axis, firstValue, lroundf(secondValue));
        if (motionKind == MotionKind::HOME_SEEK) {
            Serial.printf("OK: HOME %s\n", axis->name);
        }
    } else {
        reportError("UNKNOWN_COMMAND");
    }
}

void processSerial() {
    while (Serial.available() > 0) {
        const char value = static_cast<char>(Serial.read());
        if (value == '\n' || value == '\r') {
            if (commandOverflow) {
                reportError("COMMAND_TOO_LONG");
            } else if (commandLength > 0) {
                commandBuffer[commandLength] = '\0';
                handleCommand(commandBuffer);
            }
            commandLength = 0;
            commandOverflow = false;
        } else if (commandLength + 1 < sizeof(commandBuffer)) {
            commandBuffer[commandLength++] = value;
        } else {
            commandOverflow = true;
        }
    }
}

void updateMotion() {
    if (motionKind == MotionKind::IDLE || activeAxis == nullptr) return;

    AccelStepper& stepper = *activeAxis->stepper;
    if (motionKind == MotionKind::HOME_SEEK && homeSwitchTriggered(*activeAxis)) {
        stopPulses(stepper);
        stepper.setCurrentPosition(0);
        activeAxis->homed = true;
        completeMotion();
        return;
    }

    if (!stepper.isRunning()) {
        if (motionKind == MotionKind::HOME_SEEK) {
            activeAxis->homed = false;
            char errorMessage[48];
            snprintf(errorMessage, sizeof(errorMessage), "HOME_%s_SAFEGUARD_REACHED", activeAxis->name);
            reportError(errorMessage);
            motionKind = MotionKind::IDLE;
            activeAxis = nullptr;
        } else {
            completeMotion();
        }
    }
}

void setup() {
    Serial.begin(115200);
    Serial.println("BOOT: EufyRobot three-axis controller");
    pinMode(PIN_L1_PM_HOME, INPUT_PULLUP);
    pinMode(PIN_L2_LM_HOME, INPUT_PULLUP);
    pinMode(PIN_L3_RM_HOME, INPUT_PULLUP);
    pinMode(PIN_RUN_LED, OUTPUT);
    pinMode(PIN_HOME_LED, OUTPUT);
    pinMode(PIN_IDLE_LED, OUTPUT);
    setOperationState(OperationState::IDLE);
    configureStepper(stepperPM);
    configureStepper(stepperRM);
    configureStepper(stepperLM);
    ledcSetup(GATE_PWM_CHANNEL, GATE_PWM_FREQ_HZ, GATE_PWM_RESOLUTION_BITS);
    ledcAttachPin(PIN_GATE_SERVO, GATE_PWM_CHANNEL);
    setGateAngle(GATE_CLOSED_DEG);
    Serial.println("READY");
}

void loop() {
    processSerial();
    stepperPM.run();
    stepperRM.run();
    stepperLM.run();
    updateMotion();
}
#endif
