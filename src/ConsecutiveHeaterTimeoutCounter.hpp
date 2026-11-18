#pragma once

#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <unistd.h>

// Persists the consecutive heater-timeout count across Bluetooth recovery
// child-process restarts. The manual start script removes this file after an
// operator authorizes a new run; a normal thermostat OFF cycle resets it.
class ConsecutiveHeaterTimeoutCounter {
public:
    explicit ConsecutiveHeaterTimeoutCounter(
        std::filesystem::path path = "logs/kiln_heater_timeout_streak.txt")
        : path_(std::move(path))
    {
        if (!std::filesystem::exists(path_)) return; // No previous timeout streak.
        std::ifstream input(path_);
        if (!input) {
            throw std::runtime_error(
                "Cannot read persisted heater-timeout count: " + path_.string());
        }
        if (!(input >> count_) || count_ < 0) {
            throw std::runtime_error(
                "Invalid persisted consecutive heater-timeout count: " +
                path_.string());
        }
    }

    [[nodiscard]] int count() const { return count_; }

    void recordTimeout()
    {
        ++count_;
        persist();
    }

    void reset()
    {
        if (count_ == 0) return;
        count_ = 0;
        persist();
    }

private:
    void persist() const
    {
        if (!path_.parent_path().empty()) {
            std::filesystem::create_directories(path_.parent_path());
        }
        auto temporary = path_;
        temporary += ".tmp-" + std::to_string(getpid());
        try {
            {
                std::ofstream output(temporary, std::ios::trunc);
                if (!output) {
                    throw std::runtime_error(
                        "Cannot write heater-timeout count: " + temporary.string());
                }
                output << count_ << '\n';
                output.flush();
                if (!output) {
                    throw std::runtime_error(
                        "Cannot flush heater-timeout count: " + temporary.string());
                }
            }
            std::filesystem::rename(temporary, path_);
        } catch (...) {
            std::error_code ignored;
            std::filesystem::remove(temporary, ignored);
            throw;
        }
    }

    std::filesystem::path path_;
    int count_ = 0;
};
