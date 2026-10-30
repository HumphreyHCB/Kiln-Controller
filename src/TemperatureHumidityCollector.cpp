
#include "TemperatureHumidityCollector.hpp"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <tuple>
#include <vector>

#include <nlohmann/json.hpp>

namespace {

// Read the beacon MAC from our JSON configuration
std::string returnBeaconMac(const std::string& configPath)
{
    std::ifstream file(configPath);

    if (!file) {
        throw std::runtime_error(
            "Cannot open " + configPath
        );
    }

    auto settings = nlohmann::json::parse(file);

    return settings.at("kiln").get<std::string>();
}


// Convert a MAC address into a BlueZ device object path
std::string makeDevicePath(std::string mac)
{
    // BlueZ uses underscores instead of colons
    for (char& character : mac) {
        if (character == ':') {
            character = '_';
        }
        else {
            character = static_cast<char>(
                std::toupper(
                    static_cast<unsigned char>(character)
                )
            );
        }
    }

    return "/org/bluez/hci0/dev_" + mac;
}


// Your existing 18-byte ThermoBeacon decoder
std::tuple<double, double> decoder(
    const std::vector<std::uint8_t>& bytes)
{
    if (bytes.size() != 18) {
        throw std::invalid_argument(
            "Expected 18-byte ThermoBeacon data"
        );
    }

    int rawTemp = bytes[10] | (bytes[11] << 8);

    if (rawTemp >= 32768) {
        rawTemp -= 65536;
    }

    double temperature = rawTemp / 16.0;

    int rawHumidity = bytes[12] | (bytes[13] << 8);

    double humidity = rawHumidity / 16.0;

    return {temperature, humidity};
}

} // End anonymous namespace


// Constructor
TemperatureHumidityCollector::TemperatureHumidityCollector(
    const std::string& configPath)
    : targetMac_(returnBeaconMac(configPath))
{
}


// Destructor
TemperatureHumidityCollector::~TemperatureHumidityCollector()
{
    stop();
}


// Begin continuously listening for advertisements
void TemperatureHumidityCollector::start()
{
    if (connection_) {
        throw std::logic_error(
            "Temperature collector is already running"
        );
    }

    // Discard readings from any previous monitoring session
    {
        std::lock_guard<std::mutex> lock(readingMutex_);
        latestReading_.reset();
    }

    try {
        // Open a persistent system D-Bus connection
        connection_ = sdbus::createSystemBusConnection();

        // Create proxy for the Bluetooth adapter
        adapter_ = sdbus::createProxy(
            *connection_,
            sdbus::ServiceName{"org.bluez"},
            sdbus::ObjectPath{"/org/bluez/hci0"}
        );

        bool powered = adapter_->getProperty("Powered")
                              .onInterface("org.bluez.Adapter1")
                              .get<bool>();

        if (!powered) {
            throw std::runtime_error(
                "Bluetooth adapter is not powered"
            );
        }

        // Construct the D-Bus path for our specific beacon
        std::string devicePath = makeDevicePath(targetMac_);

        device_ = sdbus::createProxy(
            *connection_,
            sdbus::ServiceName{"org.bluez"},
            sdbus::ObjectPath{devicePath}
        );

        // Subscribe to changes in the beacon's properties
        device_->uponSignal("PropertiesChanged")
            .onInterface("org.freedesktop.DBus.Properties")
            .call(
                [this](
                    const std::string& interfaceName,
                    const std::map<std::string, sdbus::Variant>& changed,
                    const std::vector<std::string>& invalidated
                ) {
                    if (interfaceName != "org.bluez.Device1") {
                        return;
                    }

                    // Only interested in manufacturer data updates
                    auto property = changed.find("ManufacturerData");

                    if (property == changed.end()) {
                        return;
                    }

                    try {
                        auto manufacturerData = property->second.get<
                            std::map<std::uint16_t, sdbus::Variant>
                        >();

                        for (const auto& [manufacturerId, data]
                             : manufacturerData) {

                            if (manufacturerId != 16) {
                                continue;
                            }

                            auto bytes =
                                data.get<std::vector<std::uint8_t>>();

                            // Ignore 20-byte min/max packets
                            if (bytes.size() != 18) {
                                continue;
                            }

                            auto [temperature, humidity] =
                                decoder(bytes);

                            // Ignore physically invalid humidity
                            if (humidity < 0.0 || humidity > 100.0) {
                                continue;
                            }

                            SensorReading reading{
                                temperature,
                                humidity,
                                std::chrono::steady_clock::now()
                            };

                            // Safely update shared state
                            {
                                std::lock_guard<std::mutex> lock(
                                    readingMutex_
                                );

                                latestReading_ = reading;
                            }
                        }
                    }
                    catch (const std::exception& e) {
                        std::cerr
                            << "Bluetooth packet error: "
                            << e.what() << '\n';
                    }
                }
            );

        // Run D-Bus message processing in the background
        connection_->enterEventLoopAsync();
        eventLoopStarted_ = true;

        // Request BLE discovery and repeated data notifications
        std::map<std::string, sdbus::Variant> filter{
            {"Transport", sdbus::Variant{std::string{"le"}}},
            {"DuplicateData", sdbus::Variant{true}}
        };

        adapter_->callMethod("SetDiscoveryFilter")
                .onInterface("org.bluez.Adapter1")
                .withArguments(filter);

        adapter_->callMethod("StartDiscovery")
                .onInterface("org.bluez.Adapter1");

        discovering_ = true;

        std::cout << "Monitoring ThermoBeacon: "
                  << targetMac_ << '\n';
    }
    catch (...) {
        stop();
        throw;
    }
}


// Stop Bluetooth monitoring
void TemperatureHumidityCollector::stop() noexcept
{
    if (discovering_) {
        try {
            adapter_->callMethod("StopDiscovery")
                    .onInterface("org.bluez.Adapter1");
        }
        catch (const std::exception& e) {
            std::cerr << "Could not stop discovery: "
                      << e.what() << '\n';
        }

        discovering_ = false;
    }

    // Stop and join the D-Bus event-loop thread
    if (eventLoopStarted_) {
        try {
            connection_->leaveEventLoop();
        }
        catch (const std::exception& e) {
            std::cerr << "D-Bus shutdown error: "
                      << e.what() << '\n';
        }

        eventLoopStarted_ = false;
    }

    // Release proxies before destroying the connection
    device_.reset();
    adapter_.reset();
    connection_.reset();

    // No cached reading should remain usable after shutdown
    {
        std::lock_guard<std::mutex> lock(readingMutex_);
        latestReading_.reset();
    }
}


// Return a thread-safe copy of the latest reading
std::optional<SensorReading>
TemperatureHumidityCollector::getLatestReading() const
{
    std::lock_guard<std::mutex> lock(readingMutex_);

    return latestReading_;
}
