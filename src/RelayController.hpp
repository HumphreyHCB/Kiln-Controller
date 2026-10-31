
#pragma once

#include <string>
#include <optional>
#include <gpiod.hpp>

class RelayController {

public:

    // Represents an individual relay
    class Relay {
    public:
        void On();
        void Off();
        bool IsOn() const;

    private:
        friend class RelayController;

        explicit Relay(RelayController& owner);

        RelayController& owner_;
        int gpio_ = -1;
        bool isOn_ = false;
    };

    // Three relay objects accessible from KilnController
    Relay Heater;
    Relay Humidifier;
    Relay Fan;

    // Constructor and destructor
    explicit RelayController(
        const std::string& configPath = "config/relays.json"
    );

    ~RelayController() noexcept;

    // Turn all three relays OFF
    void AllOff();

    // Prevent accidental copying of hardware ownership
    RelayController(const RelayController&) = delete;
    RelayController& operator=(const RelayController&) = delete;

private:

    // GPIO numbers loaded from the configuration
    int heatergpio_;
    int humidifiergpio_;
    int fangpio_;

    // Owns the three GPIO lines
    std::optional<gpiod::line_request> lines_;

    // Internal method that changes a relay state
    void set(Relay& relay, bool on);
};
