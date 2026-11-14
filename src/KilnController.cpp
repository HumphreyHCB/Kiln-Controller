// Kiln controller with bounded sensor-stale detection.
// A separate shell supervisor restarts the ENTIRE process after sensor failures.
// The independent kiln-relay-master must retain its 10-second watchdog.
#include "KilnControlLogic.hpp"
#include "FanCirculationScheduler.hpp"
#include "KilnRunLogger.hpp"
#include "RelayController.hpp"
#include "TemperatureHumidityCollector.hpp"

#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;

constexpr auto kControlInterval = 5s;
constexpr auto kStopHeatingAfterNoSensor = 20s;
constexpr auto kRestartAfterNoSensor = 60s;
constexpr int kRecoverableSensorExit = 75;
constexpr int kFatalExit = 2;

volatile std::sig_atomic_t stopRequested = 0;
void signalStop(int) { stopRequested = 1; }

bool freshAndPlausible(const std::optional<SensorReading>& reading,
                       Clock::time_point now)
{
    return reading &&
           reading->receivedAt <= now &&
           now - reading->receivedAt < kStopHeatingAfterNoSensor &&
           std::isfinite(reading->temperature) &&
           std::isfinite(reading->humidity) &&
           reading->humidity >= 0.0 && reading->humidity <= 100.0;
}

// The supervisor watches the modification time of this file. Update it only
// when the sensor has ACTUALLY delivered a new, plausible advertisement.
void announceFreshSensorReading()
{
    const char* path = std::getenv("KILN_SENSOR_HEARTBEAT_FILE");
    if (!path || !*path) return; // Direct interactive running is also supported.
    std::ofstream file(path, std::ios::trunc);
    if (!file) {
        throw std::runtime_error(std::string("Cannot update sensor health file: ") + path);
    }
    file << "fresh\n";
    if (!file) {
        throw std::runtime_error(std::string("Cannot write sensor health file: ") + path);
    }
}

void apply(RelayController::Relay& relay, ControlAction action)
{
    switch (action) {
        case ControlAction::TurnOn:    relay.On();  break;
        case ControlAction::TurnOff:   relay.Off(); break;
        case ControlAction::LeaveAsIs: break;
    }
}

void sleepInterruptibly(std::chrono::milliseconds delay)
{
    // Keep shutdown responsive without busy-waiting.
    const auto until = Clock::now() + delay;
    while (!stopRequested && Clock::now() < until) {
        auto left = std::chrono::duration_cast<std::chrono::milliseconds>(until - Clock::now());
        if (left > 200ms) left = 200ms;
        if (left.count() > 0) std::this_thread::sleep_for(left);
    }
}

int run()
{
    RelayController relays;
    // The GPIO master owns the pins and starts each connection with all OFF.
    relays.AllOff();
    KilnControlLogic logic;
    KilnRunLogger logger;
    TemperatureHumidityCollector collector;
    try {
        collector.start();
    } catch (const std::exception& e) {
        relays.AllOff();
        std::cerr << "[BLUETOOTH] Cannot start collector: " << e.what()
                  << ". Requesting full-process recovery (exit 75).\n";
        return kRecoverableSensorExit;
    }

    std::cerr << "[KILN] Started. CSV: " << logger.path() << '\n';
    std::cerr << "[KILN] Sensor stale >20s: all OFF. Stale >=60s: exit 75 for recovery.\n";

    const auto startTime = Clock::now();
    FanCirculationScheduler fanCirculation(startTime);
    std::cerr << "[FAN] Fan follows heater/humidifier, plus 5-minute circulation "
                 "after 20 accumulated minutes OFF.\n";
    auto lastSensorTime = startTime;
    std::optional<Clock::time_point> lastAnnouncedReceipt;
    bool wasInSensorFault = false;
    int exitCode = EXIT_SUCCESS;

    while (!stopRequested) {
        const auto now = Clock::now();
        std::optional<SensorReading> reading;
        try {
            reading = collector.getLatestReading();
        } catch (const std::exception& e) {
            relays.AllOff();
            std::cerr << "[BLUETOOTH] Failed to obtain reading: " << e.what()
                      << ". Requesting full-process recovery (exit 75).\n";
            exitCode = kRecoverableSensorExit;
            break;
        }
        const bool fresh = freshAndPlausible(reading, now);

        // A repeated cached value is NOT a new Bluetooth advertisement.
        if (fresh && (!lastAnnouncedReceipt || reading->receivedAt > *lastAnnouncedReceipt)) {
            lastAnnouncedReceipt = reading->receivedAt;
            lastSensorTime = reading->receivedAt;
            announceFreshSensorReading();
        }

        // Mask stale/invalid readings from the thermostat: no sensor means
        // both heater and humidifier OFF (and we explicitly turn the fan OFF).
        const std::optional<SensorReading> controlReading =
            fresh ? reading : std::nullopt;
        const ControlDecision decision = logic.decide(
            controlReading, relays.Heater.IsOn(), relays.Humidifier.IsOn());

        if (decision.sensorFault) {
            relays.AllOff();  // Also acts as the relay-master heartbeat.
            fanCirculation.reset(Clock::now()); // Never circulate on invalid data.
            if (!wasInSensorFault) {
                std::cerr << "[SENSOR] Missing, stale or invalid reading: all relays OFF.\n";
            }
        } else {
            apply(relays.Heater, decision.heater);
            apply(relays.Humidifier, decision.humidifier);

            // Heating and humidification each require air circulation.
            // A separate timer guarantees a five-minute ventilation pulse
            // after 20 cumulative minutes of fan OFF time.
            const bool heaterOn = relays.Heater.IsOn();
            const bool humidifierOn = relays.Humidifier.IsOn();
            const bool fanOn = relays.Fan.IsOn();
            const bool wasPulsing = fanCirculation.pulseActive();
            const auto fanNow = Clock::now();
            const bool wantFan = fanCirculation.shouldRun(
                fanNow, heaterOn || humidifierOn, fanOn);
            if (wantFan && !fanOn) relays.Fan.On();
            if (!wantFan && fanOn) relays.Fan.Off();
            fanCirculation.noteApplied(Clock::now(), wantFan);

            if (!wasPulsing && fanCirculation.pulseActive()) {
                std::cerr << "[FAN] Starting 5-minute circulation pulse.\n";
            } else if (wasPulsing && !fanCirculation.pulseActive()) {
                std::cerr << "[FAN] Circulation pulse completed.\n";
            }
            if (wasInSensorFault) {
                std::cerr << "[SENSOR] Fresh data resumed. Normal control restored.\n";
            }
        }
        wasInSensorFault = decision.sensorFault;

        // Preserve the raw reading (and its increasing age) in the CSV even
        // when the controller refuses to use it for temperature decisions.
        logger.log(reading,
                   relays.Heater.IsOn(),
                   relays.Humidifier.IsOn(),
                   relays.Fan.IsOn(),
                   decision.sensorFault);

        // Restore the live console measurements from the original controller.
        // The supervisor simply passes these lines through to the terminal.
        if (reading) {
            const auto ageSeconds = std::chrono::duration_cast<std::chrono::seconds>(
                Clock::now() - reading->receivedAt).count();
            std::cout << "Temperature: " << reading->temperature << " °C\n"
                      << "Humidity: " << reading->humidity << " %\n"
                      << "Reading age: " << (ageSeconds < 0 ? 0 : ageSeconds)
                      << " seconds\n";
        } else {
            std::cout << "No reading received yet.\n";
        }
        std::cout << std::boolalpha
                  << "Heater ON: " << relays.Heater.IsOn() << '\n'
                  << "Humidifier ON: " << relays.Humidifier.IsOn() << '\n'
                  << "Fan ON: " << relays.Fan.IsOn() << '\n'
                  << "Sensor fault: " << decision.sensorFault << "\n\n"
                  << std::flush;

        // No fresh advertisements for 60 seconds => exit for FULL PROCESS
        // restart. This also works if Bluetooth never supplied a first reading.
        const auto noSensorFor = Clock::now() - lastSensorTime;
        if (noSensorFor >= kRestartAfterNoSensor) {
            relays.AllOff();
            std::cerr << "[RECOVERY] No new valid sensor data for >=60 seconds. "
                         "All relays OFF; exiting with code 75 for supervisor restart.\n";
            exitCode = kRecoverableSensorExit;
            break;
        }
        sleepInterruptibly(std::chrono::duration_cast<std::chrono::milliseconds>(kControlInterval));
    }

    // Crucially, switch OFF before any potentially blocking BlueZ StopDiscovery.
    relays.AllOff();
    std::cerr << "[KILN] Relays commanded OFF; stopping Bluetooth collector.\n";
    try {
        collector.stop();
    } catch (const std::exception& e) {
        std::cerr << "[BLUETOOTH] Error during stop(): " << e.what() << '\n';
        // Already OFF; do not lose the requested recovery exit status.
    }
    return exitCode;
}
} // namespace

int main()
{
    std::signal(SIGINT, signalStop);
    std::signal(SIGTERM, signalStop);
    try {
        return run();
    } catch (const std::exception& e) {
        // Any unhandled error, especially relay-master socket failure, is fatal.
        // Destructing RelayController closes the socket; the independent
        // relay master will command ALL OFF on disconnect / heartbeat timeout.
        std::cerr << "[FATAL] Controller error: " << e.what()
                  << ". Exiting without automatic retries.\n";
        return kFatalExit;
    }
}
