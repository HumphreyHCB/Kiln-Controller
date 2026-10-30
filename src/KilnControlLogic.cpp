#include "KilnControlLogic.hpp"

#include <cmath>
#include <fstream>
#include <stdexcept>

#include <nlohmann/json.hpp>

KilnControlLogic::KilnControlLogic(const std::string& configPath)
{
    std::ifstream file(configPath);
    if (!file) {
        throw std::runtime_error("Cannot open kiln control config: " + configPath);
    }

    const nlohmann::json config = nlohmann::json::parse(file);
    const auto& temp = config.at("temperature");
    const auto& humidity = config.at("humidity");

    temperature_ = {
        temp.at("min").get<double>(),
        temp.at("max").get<double>(),
        temp.at("stopBeforeMax").get<double>()
    };
    humidity_ = {
        humidity.at("min").get<double>(),
        humidity.at("max").get<double>(),
        humidity.at("stopBeforeMax").get<double>()
    };

    const int maxAgeSeconds = config.at("maxReadingAgeSeconds").get<int>();
    if (maxAgeSeconds <= 0) {
        throw std::invalid_argument("maxReadingAgeSeconds must be positive");
    }
    maxReadingAge_ = std::chrono::seconds(maxAgeSeconds);

    // The ON threshold is min, and the OFF threshold is max - stopBeforeMax.
    // Require a positive gap between them (hysteresis).
    auto validate = [](const Range& range, const char* name) {
        if (!std::isfinite(range.min) || !std::isfinite(range.max) ||
            !std::isfinite(range.stopBeforeMax) ||
            range.min >= range.max ||
            range.stopBeforeMax < 0.0 ||
            range.stopBeforeMax >= range.max - range.min) {
            throw std::invalid_argument(
                std::string("Invalid control range for ") + name
            );
        }
    };

    validate(temperature_, "temperature");
    validate(humidity_, "humidity");

    if (humidity_.min < 0.0 || humidity_.max > 100.0) {
        throw std::invalid_argument("Humidity range must be within 0 to 100 percent");
    }
}

ControlAction KilnControlLogic::decideOne(
    double measured,
    const Range& desired,
    bool currentlyOn)
{
    const double stopAt = desired.max - desired.stopBeforeMax;

    if (currentlyOn) {
        // Shut off within the desired range, leaving room for heat/moisture
        // already in the system to continue affecting the reading.
        return measured >= stopAt
            ? ControlAction::TurnOff
            : ControlAction::LeaveAsIs;
    }

    // Only start again once below the minimum of the requested range.
    return measured < desired.min
        ? ControlAction::TurnOn
        : ControlAction::LeaveAsIs;
}

ControlDecision KilnControlLogic::decide(
    const std::optional<SensorReading>& reading,
    bool heaterIsOn,
    bool humidifierIsOn) const
{
    // return for sensor error
    const auto offForFault = ControlDecision{
        ControlAction::TurnOff,
        ControlAction::TurnOff,
        true
    };

    if (!reading) {
        return offForFault;
    }

    // is the reading time recent or invaild
    const auto now = std::chrono::steady_clock::now();
    if (reading->receivedAt > now ||
        now - reading->receivedAt > maxReadingAge_) {
        return offForFault;
    }

    // are the temperature and humidity abs numbers
    if (!std::isfinite(reading->temperature) ||
        !std::isfinite(reading->humidity) ||
        reading->humidity < 0.0 || reading->humidity > 100.0) {
        return offForFault;
    }

    return {
        decideOne(reading->temperature, temperature_, heaterIsOn),
        decideOne(reading->humidity, humidity_, humidifierIsOn),
        false
    };
}
