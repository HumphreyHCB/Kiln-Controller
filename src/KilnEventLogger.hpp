#pragma once

#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <unistd.h>

#include <nlohmann/json.hpp>

// Persistent event journal for short-lived faults. CSV and systemd output alone
// can miss a transient incident between 20-minute GitHub status updates.
// This class does not control GPIO and never throws during an event write.
class KilnEventLogger {
public:
    explicit KilnEventLogger(
        std::filesystem::path path = "logs/kiln_control_events.jsonl")
        : path_(std::move(path)) {}

    void record(const std::string& kind, const std::string& detail) noexcept
    {
        try {
            std::filesystem::create_directories(path_.parent_path());
            const auto now = std::chrono::system_clock::now();
            const auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
                now.time_since_epoch()).count();
            const auto time = std::chrono::system_clock::to_time_t(now);
            std::tm utc{};
            if (gmtime_r(&time, &utc) == nullptr) {
                throw std::runtime_error("Cannot format kiln event time");
            }
            std::ostringstream stamp;
            stamp << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");

            const nlohmann::json event = {
                {"id", std::to_string(micros) + "-" +
                       std::to_string(getpid()) + "-" +
                       std::to_string(++sequence_)},
                {"kind", kind},
                {"detected_utc", stamp.str()},
                {"detail", detail}
            };
            std::ofstream stream(path_, std::ios::app);
            if (!stream) {
                throw std::runtime_error("Cannot open event journal");
            }
            stream << event.dump() << '\n';
            stream.flush();
            if (!stream) {
                throw std::runtime_error("Cannot flush event journal");
            }
        } catch (const std::exception& error) {
            std::cerr << "[KILN EVENT] Could not persist " << kind << ": "
                      << error.what() << '\n';
        }
    }

private:
    std::filesystem::path path_;
    unsigned long sequence_ = 0;
};
