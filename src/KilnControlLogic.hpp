#pragma once

#include "TemperatureHumidityCollector.hpp" // SensorReading

#include <chrono>
#include <optional>
#include <string>

// Commands for ON/OFF hardware. A third option is needed to stop heating/humidifying.
enum class ControlAction {
    LeaveAsIs,
    TurnOn,
    TurnOff
};

struct ControlDecision {
    ControlAction heater;
    ControlAction humidifier;
    bool sensorFault;
};

// Only decides what the hardware should do; never touches GPIO or Bluetooth.
class KilnControlLogic {
public:
    explicit KilnControlLogic(
        const std::string& configPath = "config/kiln-control.json"
    );

    // heaterIsOn/humidifierIsOn must reflect the current actuator states.
    // This function is non-blocking and does not access hardware.
    [[nodiscard]] ControlDecision decide(
        const std::optional<SensorReading>& reading,
        bool heaterIsOn,
        bool humidifierIsOn
    ) const;

    // Runtime settings are loaded once from kiln-control.json at startup.
    [[nodiscard]] std::chrono::minutes maxContinuousHeaterOn() const {
        return maxContinuousHeaterOn_;
    }
    [[nodiscard]] int maxConsecutiveHeaterTimeouts() const {
        return maxConsecutiveHeaterTimeouts_;
    }
    [[nodiscard]] double temperatureMin() const { return temperature_.min; }
    [[nodiscard]] double temperatureMax() const { return temperature_.max; }
    [[nodiscard]] double temperatureStopBeforeMax() const { return temperature_.stopBeforeMax; }
    [[nodiscard]] double temperatureStopAt() const {
        return temperature_.max - temperature_.stopBeforeMax;
    }
    [[nodiscard]] std::chrono::minutes fanOffBeforePulse() const {
        return fanOffBeforePulse_;
    }
    [[nodiscard]] std::chrono::minutes fanPulseLength() const {
        return fanPulseLength_;
    }
    [[nodiscard]] bool temperatureInRange(double temperature) const {
        return temperature >= temperature_.min && temperature <= temperature_.max;
    }

private:
    struct Range {
        double min;
        double max;
        double stopBeforeMax;
    };

    Range temperature_{};
    Range humidity_{};
    std::chrono::seconds maxReadingAge_{60};
    std::chrono::minutes maxContinuousHeaterOn_{60};
    int maxConsecutiveHeaterTimeouts_{3};
    std::chrono::minutes fanOffBeforePulse_{20};
    std::chrono::minutes fanPulseLength_{5};

    static ControlAction decideOne(
        double measured,
        const Range& desired,
        bool currentlyOn
    );
};
