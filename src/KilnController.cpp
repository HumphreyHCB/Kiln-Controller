// Kiln controller with bounded sensor-stale detection.
// A separate shell supervisor restarts the ENTIRE process after sensor failures.
// The independent kiln-relay-master must retain its 10-second watchdog.
#include "KilnControlLogic.hpp"
#include "ConsecutiveHeaterTimeoutCounter.hpp"
#include "FanCirculationScheduler.hpp"
#include "HeaterOnTimeWatchdog.hpp"
#include "HumidifierNoResponseWatchdog.hpp"
#include "KilnEventLogger.hpp"
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
    KilnRunLogger logger(logic.temperatureMin(), logic.temperatureMax(),
                         logic.temperatureStopBeforeMax(),
                         logic.maxContinuousHeaterOn(),
                         logic.maxConsecutiveHeaterTimeouts());
    KilnEventLogger eventLogger;
    HumidifierNoResponseWatchdog humidifierWatchdog(
        logic.humidifierNoIncreaseTimeout(),
        logic.humidifierMinimumRisePercentagePoints(),
        logic.humidifierRetryDelay(),
        logic.maxConsecutiveHumidifierNoIncreaseAttempts());
    ConsecutiveHeaterTimeoutCounter heaterTimeoutCounter;
    if (heaterTimeoutCounter.count() >= logic.maxConsecutiveHeaterTimeouts()) {
        relays.AllOff();
        eventLogger.record("heater_timeout_shutdown",
            "Startup blocked because the persisted consecutive heater-timeout count " +
            std::to_string(heaterTimeoutCounter.count()) +
            " has reached the configured limit. Use the manual start command after "
            "operator inspection to reset the count.");
        std::cerr << "[HEATER WATCHDOG] Persisted timeout limit reached; "
                     "all relays OFF. Operator intervention is required.\\n";
        return kFatalExit;
    }
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
    FanCirculationScheduler fanCirculation(
        startTime, logic.fanOffBeforePulse(), logic.fanPulseLength());
    HeaterOnTimeWatchdog heaterWatchdog(logic.maxContinuousHeaterOn());
    std::cerr << "[FAN] Fan follows heater/humidifier, plus "
              << logic.fanPulseLength().count() << "-minute circulation after "
              << logic.fanOffBeforePulse().count() << " accumulated minutes OFF.\n";
    std::cerr << "[HEATER WATCHDOG] Max heater ON after first reaching min: "
              << logic.maxContinuousHeaterOn().count()
              << " minutes; force OFF only with fresh, in-range temperature.\n";
    std::cerr << "[HUMIDIFIER WATCHDOG] Require at least "
              << logic.humidifierMinimumRisePercentagePoints()
              << " RH percentage-point rise within each "
              << logic.humidifierNoIncreaseTimeout().count()
              << "-minute ON window; retry after "
              << logic.humidifierRetryDelay().count() << " minutes; lock out after "
              << logic.maxConsecutiveHumidifierNoIncreaseAttempts()
              << " consecutive failures.\n";
    auto lastSensorTime = startTime;
    std::optional<Clock::time_point> lastAnnouncedReceipt;
    bool wasInSensorFault = false;
    bool sensorStaleIncidentRecorded = false;
    int consecutiveHeaterTimeouts = heaterTimeoutCounter.count();
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
        bool shutdownForRepeatedHeaterTimeout = false;

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
        const bool heaterWasOn = relays.Heater.IsOn();
        ControlDecision decision = logic.decide(
            controlReading, heaterWasOn, relays.Humidifier.IsOn());

        // Start the timeout when this heating cycle FIRST reaches the
        // configured minimum with a fresh reading, not at heater startup.
        // The time then runs continuously until the heater is truly OFF.
        // Expire only when the current fresh reading is within range.
        const bool watchdogExpired = heaterWatchdog.shouldForceOff(
            now, heaterWasOn, fresh,
            fresh && logic.temperatureInRange(reading->temperature));
        const bool forceHeaterOffForTimeout =
            !decision.sensorFault &&
            decision.heater != ControlAction::TurnOff && watchdogExpired;
        if (forceHeaterOffForTimeout) {
            decision.heater = ControlAction::TurnOff;
            std::cerr << "[HEATER WATCHDOG] Maximum in-range ON time ("
                      << logic.maxContinuousHeaterOn().count()
                      << " minutes) reached at " << reading->temperature
                      << " C; forcing heater OFF. Normal thermostat resumes "
                         "heating only when temperature falls below min.\n";
        }

        if (decision.sensorFault) {
            relays.AllOff();  // Also acts as the relay-master heartbeat.
            (void)humidifierWatchdog.observe(
                Clock::now(), false, false, false, 0.0);
            heaterWatchdog.observe(Clock::now(), false, false, false);
            fanCirculation.reset(Clock::now()); // Never circulate on invalid data.
            // Write a durable incident after 20 seconds without fresh data,
            // even if the C++ process later recovers before GitHub next polls.
            if (!sensorStaleIncidentRecorded &&
                now - lastSensorTime >= kStopHeatingAfterNoSensor) {
                const auto secs = std::chrono::duration_cast<std::chrono::seconds>(
                    now - lastSensorTime).count();
                eventLogger.record("sensor_stale",
                    "Bluetooth temperature/humidity reading unavailable or stale for " +
                    std::to_string(secs) +
                    " seconds; all relays commanded OFF.");
                sensorStaleIncidentRecorded = true;
            }
            if (!wasInSensorFault) {
                std::cerr << "[SENSOR] Missing, stale or invalid reading: all relays OFF.\n";
            }
        } else {
            apply(relays.Heater, decision.heater);
            // Track the GPIO-owner's acknowledged commanded state, not
            // merely the last thermostat decision.
            heaterWatchdog.observe(
                Clock::now(), relays.Heater.IsOn(), fresh,
                fresh && logic.temperatureInRange(reading->temperature));
            if (forceHeaterOffForTimeout) {
                heaterTimeoutCounter.recordTimeout();
                consecutiveHeaterTimeouts = heaterTimeoutCounter.count();
                const bool shutdownNow =
                    consecutiveHeaterTimeouts >= logic.maxConsecutiveHeaterTimeouts();
                eventLogger.record("heater_timeout",
                    "Heater forced OFF after " +
                    std::to_string(logic.maxContinuousHeaterOn().count()) +
                    " minutes since first reaching minimum temperature; " +
                    "current temperature " + std::to_string(reading->temperature) +
                    " C; consecutive timeout " +
                    std::to_string(consecutiveHeaterTimeouts) + " of " +
                    std::to_string(logic.maxConsecutiveHeaterTimeouts()) + ".");
                if (shutdownNow) {
                    relays.AllOff();
                    fanCirculation.reset(Clock::now());
                    eventLogger.record("heater_timeout_shutdown",
                        "Kiln shut down after " +
                        std::to_string(consecutiveHeaterTimeouts) +
                        " consecutive heater-timeout trips. All relays commanded OFF; "
                        "manual operator intervention is required before another start.");
                    std::cerr << "[HEATER WATCHDOG] "
                              << consecutiveHeaterTimeouts
                              << " consecutive heater-timeout trips reached; "
                                 "all relays OFF and stopping for manual intervention.\n";
                    shutdownForRepeatedHeaterTimeout = true;
                    exitCode = kFatalExit;
                }
            } else if (heaterWasOn &&
                       decision.heater == ControlAction::TurnOff &&
                       !relays.Heater.IsOn()) {
                // A normal thermostat completion breaks the timeout streak.
                heaterTimeoutCounter.reset();
                consecutiveHeaterTimeouts = heaterTimeoutCounter.count();
            }
            sensorStaleIncidentRecorded = false;

            const bool temperatureAllowsHumidification =
                logic.temperatureInRange(reading->temperature);
            const bool humidityTargetReached =
                reading->humidity >= logic.humidityStopAt();
            if (temperatureAllowsHumidification && humidityTargetReached) {
                // A normal thermostat target is evidence the humidifier
                // responded, so it clears prior failed attempts.
                humidifierWatchdog.reportTargetReached();
            }
            const bool humidifierWasOn = relays.Humidifier.IsOn();
            const auto humidifierObservation = humidifierWatchdog.observe(
                Clock::now(), humidifierWasOn, fresh,
                temperatureAllowsHumidification &&
                    decision.humidifier != ControlAction::TurnOff,
                reading->humidity);
            if (humidifierObservation.failedAttempt) {
                std::cerr << "[HUMIDIFIER WATCHDOG] No meaningful RH rise in "
                          << logic.humidifierNoIncreaseTimeout().count()
                          << " minutes (baseline "
                          << humidifierObservation.baselineHumidity
                          << "%, peak " << humidifierObservation.peakHumidity
                          << "%). Turning humidifier OFF; consecutive failed attempts: "
                          << humidifierObservation.consecutiveFailures << "/"
                          << logic.maxConsecutiveHumidifierNoIncreaseAttempts()
                          << ".\n";
                decision.humidifier = ControlAction::TurnOff;
            }
            if (humidifierObservation.lockedOutNow) {
                eventLogger.record(
                    "humidifier_no_response",
                    "After " + std::to_string(
                        logic.maxConsecutiveHumidifierNoIncreaseAttempts()) +
                    " consecutive " + std::to_string(
                        logic.humidifierNoIncreaseTimeout().count()) +
                    "-minute humidifier ON windows, humidity rose by less than " +
                    std::to_string(
                        logic.humidifierMinimumRisePercentagePoints()) +
                    " percentage point. Final no-response window: RH baseline " +
                    std::to_string(humidifierObservation.baselineHumidity) +
                    "%, peak " +
                    std::to_string(humidifierObservation.peakHumidity) +
                    "% (rise " + std::to_string(
                        humidifierObservation.peakHumidity -
                        humidifierObservation.baselineHumidity) +
                    " percentage points). The humidifier is locked OFF for this "
                    "controller run; temperature control and heating continue. "
                    "Check/refill the water tank, then manually restart the controller.");
                std::cerr << "[HUMIDIFIER WATCHDOG] Repeated no-response limit reached; "
                             "humidifier locked OFF for this controller run. Heater "
                             "continues under normal temperature control.\n";
            }
            if (humidifierWatchdog.lockedOut()) {
                decision.humidifier = ControlAction::TurnOff;
            } else if (!humidifierObservation.failedAttempt &&
                       decision.humidifier == ControlAction::TurnOn &&
                       humidifierWatchdog.blocksStart(Clock::now())) {
                decision.humidifier = ControlAction::TurnOff;
            }

            if (shutdownForRepeatedHeaterTimeout) {
                // Do not let the rest of the normal control pass switch any
                // relay back ON after the all-relay shutdown command.
                relays.AllOff();
            } else {
                apply(relays.Humidifier, decision.humidifier);

                // Heating and humidification each require air circulation.
                // A separate timer guarantees a configurable ventilation pulse
                // after the configured accumulated fan-OFF interval.
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
                    std::cerr << "[FAN] Starting "
                              << logic.fanPulseLength().count()
                              << "-minute circulation pulse.\n";
                } else if (wasPulsing && !fanCirculation.pulseActive()) {
                    std::cerr << "[FAN] Circulation pulse completed.\n";
                }
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
                  << "Sensor fault: " << decision.sensorFault << "\n"
                  << "Consecutive heater timeouts: "
                  << consecutiveHeaterTimeouts << "/"
                  << logic.maxConsecutiveHeaterTimeouts() << "\n\n"
                  << std::flush;

        if (shutdownForRepeatedHeaterTimeout) {
            break; // Fatal exit; supervisor confirms ALL_OFF and does not retry.
        }

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
