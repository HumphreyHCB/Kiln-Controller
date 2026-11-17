#pragma once

#include <chrono>
#include <optional>
#include <stdexcept>

// Additional software ON-time limit; NOT an independent thermal cutoff.
// Time starts only at the FIRST fresh in-range reading within a heating cycle,
// rather than during the below-minimum warm-up. The limit continues counting
// from that first in-range reading even if the temperature briefly dips.
// An actual OFF transition resets the entire cycle.
class HeaterOnTimeWatchdog {
public:
    using Clock = std::chrono::steady_clock;
    using Duration = Clock::duration;

    explicit HeaterOnTimeWatchdog(Duration maxInRangeOn)
        : maxInRangeOn_(maxInRangeOn)
    {
        if (maxInRangeOn_ <= Duration::zero()) {
            throw std::invalid_argument("Heater maximum in-range ON time must be positive");
        }
    }

    void observe(Clock::time_point now, bool heaterOn,
                 bool freshSensor, bool temperatureInRange)
    {
        if (!heaterOn) {
            inRangeSince_.reset();
        } else if (!inRangeSince_ && freshSensor && temperatureInRange) {
            inRangeSince_ = now;
        }
    }

    [[nodiscard]] bool shouldForceOff(Clock::time_point now,
                                      bool heaterOn,
                                      bool freshSensor,
                                      bool temperatureInRange)
    {
        observe(now, heaterOn, freshSensor, temperatureInRange);
        return heaterOn && freshSensor && temperatureInRange &&
               inRangeSince_.has_value() &&
               now - *inRangeSince_ >= maxInRangeOn_;
    }

private:
    Duration maxInRangeOn_;
    std::optional<Clock::time_point> inRangeSince_;
};
