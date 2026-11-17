#include "KilnEventLogger.hpp"

#include <cassert>
#include <filesystem>
#include <fstream>
#include <string>
#include <unistd.h>
#include <nlohmann/json.hpp>

int main()
{
    const auto path = std::filesystem::temp_directory_path() /
                      ("kiln-events-test-" + std::to_string(getpid()) + ".jsonl");
    std::filesystem::remove(path);
    KilnEventLogger journal(path);
    journal.record("sensor_stale", "Bluetooth reading stale; relays OFF");
    journal.record("heater_timeout", "Heater timeout; relay OFF");

    std::ifstream input(path);
    std::string firstLine;
    std::string secondLine;
    assert(static_cast<bool>(std::getline(input, firstLine)));
    assert(static_cast<bool>(std::getline(input, secondLine)));
    const auto first = nlohmann::json::parse(firstLine);
    const auto second = nlohmann::json::parse(secondLine);

    assert(first["kind"] == "sensor_stale");
    assert(first["detail"] == "Bluetooth reading stale; relays OFF");
    assert(second["kind"] == "heater_timeout");
    assert(first["id"] != second["id"]);
    assert(first["detected_utc"].get<std::string>().size() == 20);
    std::filesystem::remove(path);
    return 0;
}
