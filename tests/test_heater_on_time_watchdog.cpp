#include "FanCirculationScheduler.hpp"
#include "HeaterOnTimeWatchdog.hpp"

#include <cassert>
#include <chrono>
#include <stdexcept>

using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;

int main()
{
    const Clock::time_point start{};

    // The 60-minute timer starts when the heater actually turns ON.
    HeaterOnTimeWatchdog watchdog(60min);
    assert(!watchdog.shouldForceOff(start, false, true, true));
    watchdog.observe(start, true);
    assert(!watchdog.shouldForceOff(start + 59min, true, true, true));
    assert(!watchdog.shouldForceOff(start + 60min, true, true, false));

    // Sensor faults do NOT allow this software timeout to authorize
    // temperature-based decisions. The existing fault logic forces all OFF.
    assert(!watchdog.shouldForceOff(start + 61min, true, false, true));

    // Even after a slow warm-up, as soon as temperature is in the
    // valid range after 60 minutes, the heater must be forced OFF.
    assert(watchdog.shouldForceOff(start + 62min, true, true, true));
    watchdog.observe(start + 62min, false);

    // The new ON cycle gets a new full timeout, and ordinary hysteresis
    // will hold it OFF until the temperature goes below the configured min.
    watchdog.observe(start + 70min, true);
    assert(!watchdog.shouldForceOff(start + 129min, true, true, true));
    assert(watchdog.shouldForceOff(start + 130min, true, true, true));

    // Explicitly reject unusable negative/zero durations.
    bool rejected = false;
    try {
        HeaterOnTimeWatchdog invalid(0min);
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    assert(rejected);

    // Fan circulation uses the configurable accumulated-OFF interval and
    // extra-ON pulse duration, not hard-coded 20/5-minute timings.
    FanCirculationScheduler fan(start, 2min, 3min);
    assert(!fan.shouldRun(start, false, false));
    fan.noteApplied(start, false);
    assert(!fan.shouldRun(start + 1min, false, false));
    fan.noteApplied(start + 1min, false);
    assert(fan.shouldRun(start + 2min, false, false));
    fan.noteApplied(start + 2min, true);
    assert(fan.pulseActive());
    assert(fan.shouldRun(start + 4min, false, true));
    fan.noteApplied(start + 4min, true);
    assert(!fan.shouldRun(start + 5min, false, true));
    fan.noteApplied(start + 5min, false);
    assert(!fan.pulseActive());

    return 0;
}
