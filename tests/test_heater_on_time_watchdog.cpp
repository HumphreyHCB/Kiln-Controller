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

    HeaterOnTimeWatchdog watchdog(60min);
    assert(!watchdog.shouldForceOff(start, false, true, false));

    // Warm-up can take much longer than an hour. Do not begin the timer
    // while the temperature is still below the minimum threshold.
    watchdog.observe(start, true, true, false);
    assert(!watchdog.shouldForceOff(start + 90min, true, true, false));
    assert(!watchdog.shouldForceOff(start + 100min, true, false, true));

    // Timer starts at first fresh, in-range sample (100 minutes after ON).
    assert(!watchdog.shouldForceOff(start + 100min, true, true, true));
    assert(!watchdog.shouldForceOff(start + 159min, true, true, true));
    assert(watchdog.shouldForceOff(start + 160min, true, true, true));

    // No forced decision on stale readings, even if the timer has expired.
    assert(!watchdog.shouldForceOff(start + 161min, true, false, true));

    // A brief dip below min does not reset an already started timer;
    // but forced OFF is still only allowed with an in-range reading.
    assert(!watchdog.shouldForceOff(start + 162min, true, true, false));
    assert(watchdog.shouldForceOff(start + 163min, true, true, true));

    // After OFF, new heating cycles must independently reach min again.
    watchdog.observe(start + 164min, false, true, true);
    assert(!watchdog.shouldForceOff(start + 170min, true, true, false));
    assert(!watchdog.shouldForceOff(start + 190min, true, true, true));
    assert(!watchdog.shouldForceOff(start + 249min, true, true, true));
    assert(watchdog.shouldForceOff(start + 250min, true, true, true));

    bool rejected = false;
    try {
        HeaterOnTimeWatchdog invalid(0min);
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    assert(rejected);

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
