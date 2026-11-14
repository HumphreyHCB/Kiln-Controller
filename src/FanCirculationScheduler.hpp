#pragma once

#include <chrono>
#include <optional>
#include <stdexcept>

// Decides only whether the fan is needed; does not access GPIO or Bluetooth.
// All timings use steady_clock, so clock/time-zone changes don't affect pulses.
//
// Fan is ON if requested for heating/humidification OR if a pulse is active.
// OFF time is accumulated (not necessarily consecutive). Brief fan ON bursts
// pause that counter; only 5 continuous minutes of airflow reset it to zero.
class FanCirculationScheduler {
public:
    using Clock = std::chrono::steady_clock;
    using Duration = Clock::duration;

    explicit FanCirculationScheduler(
        Clock::time_point now,
        Duration offBeforePulse = std::chrono::minutes(20),
        Duration pulseLength = std::chrono::minutes(5))
        : lastObserved_(now),
          offBeforePulse_(offBeforePulse),
          pulseLength_(pulseLength)
    {
        if (offBeforePulse_ <= Duration::zero() || pulseLength_ <= Duration::zero()) {
            throw std::invalid_argument("Fan circulation durations must be positive");
        }
    }

    // Observe the physical-commanded fan state, then choose the desired state.
    // Call noteApplied() after successfully applying that desired state.
    [[nodiscard]] bool shouldRun(Clock::time_point now,
                                 bool heatingOrHumidifying,
                                 bool fanCurrentlyOn)
    {
        observe(now, fanCurrentlyOn);

        if (pulseUntil_ && now >= *pulseUntil_) {
            pulseUntil_.reset();
        }
        if (!pulseUntil_ && accumulatedOff_ >= offBeforePulse_) {
            pulseUntil_ = now + pulseLength_;
        }
        return heatingOrHumidifying || pulseUntil_.has_value();
    }

    void noteApplied(Clock::time_point now, bool fanNowOn)
    {
        observe(now, fanNowOn);
    }

    // During a sensor fault all relays must remain OFF, including the fan.
    // Start a fresh OFF countdown only after valid sensor readings return.
    void reset(Clock::time_point now)
    {
        lastObserved_ = now;
        fanWasOn_ = false;
        accumulatedOff_ = Duration::zero();
        continuousOn_ = Duration::zero();
        pulseUntil_.reset();
    }

    [[nodiscard]] bool pulseActive() const { return pulseUntil_.has_value(); }

private:
    void observe(Clock::time_point now, bool fanIsOn)
    {
        const auto elapsed = now > lastObserved_ ? now - lastObserved_ : Duration::zero();

        // Time since the previous check belongs to the fan state remembered
        // at that previous check. This also counts the precise end of a pulse.
        if (fanWasOn_) {
            continuousOn_ += elapsed;
            if (continuousOn_ >= pulseLength_) {
                // A full uninterrupted fan run already ventilated the box.
                accumulatedOff_ = Duration::zero();
            }
        } else {
            accumulatedOff_ += elapsed;
        }

        // Short interruptions end a continuous ON run, but DO NOT erase the
        // accumulated OFF time. That is what avoids postponement by short bursts.
        if (fanIsOn != fanWasOn_) {
            continuousOn_ = Duration::zero();
        }
        fanWasOn_ = fanIsOn;
        lastObserved_ = now;
    }

    Clock::time_point lastObserved_;
    Duration offBeforePulse_;
    Duration pulseLength_;
    Duration accumulatedOff_ = Duration::zero();
    Duration continuousOn_ = Duration::zero();
    bool fanWasOn_ = false;
    std::optional<Clock::time_point> pulseUntil_;
};
