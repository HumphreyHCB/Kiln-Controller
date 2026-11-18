#pragma once

#include "TemperatureHumidityCollector.hpp"

#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <locale>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unistd.h> // getpid() on Linux

// One KilnRunLogger object = one CSV file for this program run.
// Construct it once in main(), then call log() after each control decision.
class KilnRunLogger {
public:
    explicit KilnRunLogger(double temperatureMinC,
                             double temperatureMaxC,
                             double temperatureStopBeforeMaxC,
                             std::chrono::minutes maxContinuousHeaterOn,
                             int maxConsecutiveHeaterTimeouts,
                             const std::string& directory = "logs")
        : temperatureMinC_(temperatureMinC),
          temperatureMaxC_(temperatureMaxC),
          temperatureStopBeforeMaxC_(temperatureStopBeforeMaxC),
          maxContinuousHeaterOnMinutes_(maxContinuousHeaterOn.count()),
          maxConsecutiveHeaterTimeouts_(maxConsecutiveHeaterTimeouts)
    {
        std::filesystem::create_directories(directory);

        // Include the time, process ID, and microseconds in the filename,
        // so even two runs started close together get separate files.
        const auto now = std::chrono::system_clock::now();
        const auto time = std::chrono::system_clock::to_time_t(now);
        const auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
            now.time_since_epoch()).count() % 1000000;
        std::tm utc{};
        if (gmtime_r(&time, &utc) == nullptr) {
            throw std::runtime_error("Cannot format log filename timestamp");
        }

        std::ostringstream filename;
        filename << "kiln_" << std::put_time(&utc, "%Y%m%d_%H%M%S")
                 << '_' << getpid() << '_' << micros << ".csv";
        path_ = (std::filesystem::path(directory) / filename.str()).string();

        file_.open(path_, std::ios::out | std::ios::trunc);
        if (!file_) {
            throw std::runtime_error("Cannot open log file: " + path_);
        }
        file_.imbue(std::locale::classic()); // Always use a '.' decimal separator
        file_ << "timestamp_utc,temperature_c,humidity_percent,reading_age_seconds,"
                 "heater_on,humidifier_on,fan_on,sensor_fault,cpu_temperature_c,"
                 "configured_temperature_min_c,configured_temperature_max_c,"
                 "configured_temperature_stop_before_max_c,"
                 "configured_temperature_heater_off_c,"
                 "max_continuous_heater_on_minutes,"
                 "max_consecutive_heater_timeouts\n";
        file_.flush();
    }

    // The sensor reading may be absent. Pass the actual relay states AFTER
    // applying the control decision, so this records what was commanded.
    void log(const std::optional<SensorReading>& reading,
             bool heaterOn, bool humidifierOn, bool fanOn, bool sensorFault)
    {
        const auto now = std::chrono::system_clock::now();
        const auto time = std::chrono::system_clock::to_time_t(now);
        const auto millis = std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch()).count() % 1000;
        std::tm utc{};
        if (gmtime_r(&time, &utc) == nullptr) {
            throw std::runtime_error("Cannot format log timestamp");
        }

        file_ << std::put_time(&utc, "%Y-%m-%dT%H:%M:%S")
              << '.' << std::setfill('0') << std::setw(3) << millis
              << std::setfill(' ') << 'Z' << ',';

        if (reading) {
            const double ageSeconds = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - reading->receivedAt).count();
            file_ << std::fixed << std::setprecision(4)
                  << reading->temperature << ',' << reading->humidity << ','
                  << std::setprecision(2) << ageSeconds;
        } else {
            file_ << ",,"; // Three empty columns: temp, humidity, age
        }

        file_ << ',' << (heaterOn ? 1 : 0)
              << ',' << (humidifierOn ? 1 : 0)
              << ',' << (fanOn ? 1 : 0)
              << ',' << (sensorFault ? 1 : 0) << ',';

        // Read the Pi's SoC temperature alongside this log row.
        // If unavailable, leave the final CSV field blank.
        if (const auto cpuTemp = readCpuTemperatureC()) {
            file_ << std::fixed << std::setprecision(2) << *cpuTemp;
        }
        file_ << ',' << std::fixed << std::setprecision(2)
              << temperatureMinC_ << ',' << temperatureMaxC_ << ','
              << temperatureStopBeforeMaxC_ << ','
              << temperatureMaxC_ - temperatureStopBeforeMaxC_
              << ',' << maxContinuousHeaterOnMinutes_
              << ',' << maxConsecutiveHeaterTimeouts_ << '\n';

        // Save each row immediately, rather than waiting for normal shutdown.
        file_.flush();
        if (!file_) {
            throw std::runtime_error("Failed to write log file: " + path_);
        }
    }

    const std::string& path() const { return path_; }

private:
    static std::optional<double> readCpuTemperatureC()
    {
        // The Raspberry Pi kernel reports this temperature in millidegrees C.
        std::ifstream sensor("/sys/class/thermal/thermal_zone0/temp");
        long millidegrees = 0;
        if (!(sensor >> millidegrees)) {
            return std::nullopt;
        }
        return static_cast<double>(millidegrees) / 1000.0;
    }

    double temperatureMinC_;
    double temperatureMaxC_;
    double temperatureStopBeforeMaxC_;
    long long maxContinuousHeaterOnMinutes_;
    int maxConsecutiveHeaterTimeouts_;
    std::string path_;
    std::ofstream file_;
};
