#pragma once

#include <algorithm>
#include <chrono>
#include <cmath>
#include <optional>
#include <stdexcept>

// Limits continuous humidifier operation when fresh RH readings show no
// meaningful progress. This class is deliberately independent of heater
// control: a humidifier fault must never request a kiln-wide shutdown.
class HumidifierNoResponseWatchdog {
public:
    using Clock = std::chrono::steady_clock;

    struct Observation {
        bool forceOff = false;
        bool failedAttempt = false;
        bool lockedOutNow = false;
        bool madeProgress = false;
        int consecutiveFailures = 0;
        double baselineHumidity = 0.0;
        double peakHumidity = 0.0;
    };

    HumidifierNoResponseWatchdog(
        std::chrono::minutes noIncreaseTimeout,
        double minimumRisePercentagePoints,
        std::chrono::minutes retryDelay,
        int maxConsecutiveFailures)
        : noIncreaseTimeout_(noIncreaseTimeout),
          minimumRisePercentagePoints_(minimumRisePercentagePoints),
          retryDelay_(retryDelay),
          maxConsecutiveFailures_(maxConsecutiveFailures)
    {
        if (noIncreaseTimeout_.count() <= 0 || retryDelay_.count() <= 0 ||
            maxConsecutiveFailures_ <= 0 ||
            !std::isfinite(minimumRisePercentagePoints_) ||
            minimumRisePercentagePoints_ <= 0.0 ||
            minimumRisePercentagePoints_ > 100.0) {
            throw std::invalid_argument("Invalid humidifier no-response settings");
        }
    }

    [[nodiscard]] bool blocksStart(Clock::time_point now) const
    {
        return lockedOut_ || (retryAt_ && now < *retryAt_);
    }

    // Call with the acknowledged relay state before applying this loop's
    // thermostat decision. Ineligible/stale periods cancel the current timer
    // without counting a failure. A target reached by normal control clears
    // the failure streak through reportTargetReached().
    Observation observe(Clock::time_point now,
                        bool humidifierIsOn,
                        bool freshReading,
                        bool temperatureAllowsHumidification,
                        double humidity)
    {
        Observation result;
        result.consecutiveFailures = consecutiveFailures_;
        if (!freshReading || !temperatureAllowsHumidification ||
            !humidifierIsOn || lockedOut_ ||
            !std::isfinite(humidity) || humidity < 0.0 || humidity > 100.0) {
            attemptStarted_.reset();
            return result;
        }

        if (!attemptStarted_) {
            attemptStarted_ = now;
            baselineHumidity_ = humidity;
            peakHumidity_ = humidity;
            return result;
        }

        peakHumidity_ = std::max(peakHumidity_, humidity);
        if (now < *attemptStarted_ || now - *attemptStarted_ < noIncreaseTimeout_) {
            return result;
        }

        result.baselineHumidity = baselineHumidity_;
        result.peakHumidity = peakHumidity_;
        if (peakHumidity_ - baselineHumidity_ >= minimumRisePercentagePoints_) {
            // Progress in this window confirms that water is being delivered.
            // Start a fresh window so only a prolonged lack of progress trips.
            baselineHumidity_ = peakHumidity_;
            peakHumidity_ = humidity;
            attemptStarted_ = now;
            consecutiveFailures_ = 0;
            retryAt_.reset();
            result.madeProgress = true;
            result.consecutiveFailures = 0;
            return result;
        }

        ++consecutiveFailures_;
        retryAt_ = now + retryDelay_;
        attemptStarted_.reset();
        result.forceOff = true;
        result.failedAttempt = true;
        result.consecutiveFailures = consecutiveFailures_;
        if (consecutiveFailures_ >= maxConsecutiveFailures_) {
            lockedOut_ = true;
            retryAt_.reset();
            result.lockedOutNow = true;
        }
        return result;
    }

    void reportTargetReached()
    {
        attemptStarted_.reset();
        consecutiveFailures_ = 0;
        retryAt_.reset();
        // A final lockout stays latched until the controller is restarted;
        // restarting is an explicit operator action in this installation.
    }

    [[nodiscard]] int consecutiveFailures() const { return consecutiveFailures_; }
    [[nodiscard]] bool lockedOut() const { return lockedOut_; }

private:
    std::chrono::minutes noIncreaseTimeout_;
    double minimumRisePercentagePoints_;
    std::chrono::minutes retryDelay_;
    int maxConsecutiveFailures_;
    int consecutiveFailures_ = 0;
    bool lockedOut_ = false;
    std::optional<Clock::time_point> attemptStarted_;
    std::optional<Clock::time_point> retryAt_;
    double baselineHumidity_ = 0.0;
    double peakHumidity_ = 0.0;
};
