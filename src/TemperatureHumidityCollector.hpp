
#pragma once

#include <chrono>
#include <memory>
#include <mutex>
#include <optional>
#include <string>

#include <sdbus-c++/sdbus-c++.h>

struct SensorReading {
    double temperature;
    double humidity;
    std::chrono::steady_clock::time_point receivedAt;
};

class TemperatureHumidityCollector {
public:
    explicit TemperatureHumidityCollector(
        const std::string& configPath = "config/beacons.json"
    );

    ~TemperatureHumidityCollector();

    // Begin continuous Bluetooth monitoring
    void start();

    // Stop monitoring and release Bluetooth resources
    void stop() noexcept;

    // Return the latest reading without waiting
    std::optional<SensorReading> getLatestReading() const;

private:
    std::string targetMac_;

    // BlueZ D-Bus objects
    std::unique_ptr<sdbus::IConnection> connection_;
    std::unique_ptr<sdbus::IProxy> adapter_;
    std::unique_ptr<sdbus::IProxy> device_;

    bool discovering_ = false;
    bool eventLoopStarted_ = false;

    // Shared state accessed by two threads
    mutable std::mutex readingMutex_;
    std::optional<SensorReading> latestReading_;
};
