#pragma once

#include <chrono>
#include <optional>
#include <stdexcept>

// Additional software timeout for an otherwise healthy, continuously ON heater.
// It is NOT a hardware thermal cutout. Use steady_clock to avoid clock changes.
//
// The time limit counts from the heater's last OFF->ON transition, including
// warm-up. After the timeout, it forces OFF only once fresh sensor readings
// confirm the kiln has reached its configured valid temperature range.
//
// The normal thermostat takes over after OFF and will not restart heating until
// temperature falls below the configured minimum (hysteresis).
class HeaterOnTimeWatchdog {
public:
    using Clock = std::chrono::steady_clock;
    using Duration = Clock::duration;

    explicit HeaterOnTimeWatchdog(Duration maxContinuousOn)
        : maxContinuousOn_(maxContinuousOn)
    {
        if (maxContinuousOn_ <= Duration::zero()) {
            throw std::invalid_argument("Heater maximum continuous ON time must be positive");
        }
    }

    void observe(Clock::time_point now, bool heaterOn)
    {
        if (!heaterOn) {
            onSince_.reset();
        } else if (!onSince_) {
            onSince_ = now;
        }
    }

    [[nodiscard]] bool shouldForceOff(Clock::time_point now,
                                       bool heaterOn,
                                       bool freshSensor,
                                       bool temperatureInRange)
    {
        observe(now, heaterOn);
        return heaterOn &&
               freshSensor &&
               temperatureInRange &&
               onSince_.has_value() &&
               now - *onSince_ >= maxContinuousOn_;
    }

private:
    Duration maxContinuousOn_;
    std::optional<Clock::time_point> onSince_;
};
