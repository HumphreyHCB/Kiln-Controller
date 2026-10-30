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

private:
    struct Range {
        double min;
        double max;
        double stopBeforeMax;
    };

    Range temperature_{};
    Range humidity_{};
    std::chrono::seconds maxReadingAge_{60};

    static ControlAction decideOne(
        double measured,
        const Range& desired,
        bool currentlyOn
    );
};
