// Real thermostat hysteresis and JSON timing settings, without GPIO hardware.
#include "KilnControlLogic.hpp"
#include "HeaterOnTimeWatchdog.hpp"

#include <cassert>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <nlohmann/json.hpp>

using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;

int main()
{
    KilnControlLogic logic("config/kiln-control.json");
    assert(logic.maxContinuousHeaterOn() == 60min);
    assert(logic.fanOffBeforePulse() == 20min);
    assert(logic.fanPulseLength() == 5min);

    // The normal thermostat would keep the heater on at 40C because the
    // repository default heater-OFF threshold is 46 - 2.5 = 43.5C.
    SensorReading reading{40.0, 74.0, Clock::now()};
    assert(logic.temperatureInRange(reading.temperature));
    assert(logic.decide(reading, true, false).heater == ControlAction::LeaveAsIs);

    // But after a continuous 60-minute ON period the watchdog forces OFF.
    HeaterOnTimeWatchdog watchdog(logic.maxContinuousHeaterOn());
    const auto t0 = Clock::now();
    watchdog.observe(t0, true, true, false);
    assert(!watchdog.shouldForceOff(t0 + 60min, true, true, false));
    watchdog.observe(t0 + 60min, true, true, true);
    assert(watchdog.shouldForceOff(t0 + 120min, true, true,
                                    logic.temperatureInRange(reading.temperature)));
    watchdog.observe(t0 + 120min, false, true, true);

    // After forcing OFF, existing hysteresis holds OFF until below minimum.
    assert(logic.decide(reading, false, false).heater == ControlAction::LeaveAsIs);
    reading.temperature = 37.9; // config min = 38
    reading.receivedAt = Clock::now();
    assert(logic.decide(reading, false, false).heater == ControlAction::TurnOn);

    // Older config files without new keys still load with fallback timings.
    nlohmann::json original;
    {
        std::ifstream input("config/kiln-control.json");
        input >> original;
    }
    const auto tempFile = std::filesystem::temp_directory_path() /
                          "kiln-control-timing-unit-test.json";
    auto fromConfig = [&](nlohmann::json config) {
        std::ofstream output(tempFile);
        output << config.dump(2);
        output.close();
        return KilnControlLogic(tempFile.string());
    };

    auto old = original;
    old["temperature"].erase("maxContinuousHeaterOnMinutes");
    old.erase("fanCirculation");
    const auto oldLogic = fromConfig(old);
    assert(oldLogic.maxContinuousHeaterOn() == 60min);
    assert(oldLogic.fanOffBeforePulse() == 20min);
    assert(oldLogic.fanPulseLength() == 5min);

    // User-specified valid nondefault durations are applied.
    original["temperature"]["maxContinuousHeaterOnMinutes"] = 45;
    original["fanCirculation"]["offBeforePulseMinutes"] = 30;
    original["fanCirculation"]["pulseMinutes"] = 8;
    const auto custom = fromConfig(original);
    assert(custom.maxContinuousHeaterOn() == 45min);
    assert(custom.fanOffBeforePulse() == 30min);
    assert(custom.fanPulseLength() == 8min);

    auto mustReject = [&](nlohmann::json config) {
        bool rejected = false;
        try {
            const auto bad = fromConfig(config);
            (void)bad;
        } catch (const std::invalid_argument&) {
            rejected = true;
        }
        assert(rejected);
    };

    auto invalid = original;
    invalid["temperature"]["maxContinuousHeaterOnMinutes"] = 0;
    mustReject(invalid);
    invalid = original;
    invalid["fanCirculation"]["offBeforePulseMinutes"] = 0;
    mustReject(invalid);
    invalid = original;
    invalid["fanCirculation"]["pulseMinutes"] = -5;
    mustReject(invalid);

    std::filesystem::remove(tempFile);
    return 0;
}
