#pragma once

#include <string>

// Same application-facing interface as before. GPIO is now owned by
// an independent systemd service, not by this C++ process.
class RelayController {
public:
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
    };

    Relay Heater;
    Relay Humidifier;
    Relay Fan;

    explicit RelayController(
        const std::string& configPath = "config/relays.json");
    ~RelayController() noexcept;

    void AllOff();

    RelayController(const RelayController&) = delete;
    RelayController& operator=(const RelayController&) = delete;

private:
    int socket_ = -1;
    void set(Relay& relay, bool on);
    bool get(const Relay& relay) const;
    std::string transact(const std::string& command) const;
};
