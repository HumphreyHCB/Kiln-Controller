
#include "RelayController.hpp"

#include <iostream>
#include <stdexcept>
#include <fstream>

#include <nlohmann/json.hpp>
#include <gpiod.hpp>


namespace {

int relayToGpio(int relayNumber)
{
    switch (relayNumber) {
        case 1: return 26;
        case 2: return 19;
        case 3: return 13;
        case 4: return 6;

        default:
            throw std::invalid_argument("Invalid relay number");
    }
}

} 

// receives a reference to the controller
// that owns this particular relay.
//
// owner_ is a reference member, so it must be initialised
// using the constructor's member initialiser list.
RelayController::Relay::Relay(RelayController& owner)
    : owner_(owner)
{
}


void RelayController::Relay::On()
{
    owner_.set(*this, true);
}


// Same principle as On(), but pass false to turn OFF.
void RelayController::Relay::Off()
{
    owner_.set(*this, false);
}


// Returns the last successfully commanded state.
bool RelayController::Relay::IsOn() const
{
    return isOn_;
}



RelayController::RelayController(const std::string& configPath)
    : Heater(*this),
      Humidifier(*this),
      Fan(*this)
{
    // *this means the current RelayController object.

    std::ifstream file(configPath);

    if (!file) {
        throw std::runtime_error(
            "Cannot open Relay control config: " + configPath
        );
    }

    const nlohmann::json config = nlohmann::json::parse(file);

    const int heaterRelayNumber =
        config.at("heaterRelay").get<int>();

    const int humidifierRelayNumber =
        config.at("humidifierRelay").get<int>();

    const int fanRelayNumber =
        config.at("fanRelay").get<int>();


    // A physical relay must not be assigned to two devices.
    if (heaterRelayNumber == humidifierRelayNumber ||
        heaterRelayNumber == fanRelayNumber ||
        humidifierRelayNumber == fanRelayNumber) {

        throw std::invalid_argument(
            "Multiple devices assigned to the same relay"
        );
    }


    heatergpio_ = relayToGpio(heaterRelayNumber);
    humidifiergpio_ = relayToGpio(humidifierRelayNumber);
    fangpio_ = relayToGpio(fanRelayNumber);

    Heater.gpio_ = heatergpio_;
    Humidifier.gpio_ = humidifiergpio_;
    Fan.gpio_ = fangpio_;



    gpiod::line_settings settings;


    // ACTIVE   = electrical LOW  = relay ON
    // INACTIVE = electrical HIGH = relay OFF
    // Start all three relays OFF.
    settings.set_direction(gpiod::line::direction::OUTPUT)
            .set_active_low(true)
            .set_output_value(gpiod::line::value::INACTIVE);


    // Request ownership of our three GPIO pins from Linux.
    lines_.emplace(
        gpiod::chip("/dev/gpiochip0")
            .prepare_request()
            .set_consumer("kiln-controller")
            .add_line_settings(
                gpiod::line::offsets{
                    static_cast<unsigned int>(heatergpio_),
                    static_cast<unsigned int>(humidifiergpio_),
                    static_cast<unsigned int>(fangpio_)
                },
                settings
            )
            .do_request()
    );


    std::cout << "RelayController initialised.\n";
    std::cout << "Heater GPIO: " << heatergpio_ << '\n';
    std::cout << "Humidifier GPIO: " << humidifiergpio_ << '\n';
    std::cout << "Fan GPIO: " << fangpio_ << '\n';
    std::cout << "All relays initially OFF.\n";
}



void RelayController::set(Relay& relay, bool on)
{
    if (!lines_) {
        throw std::runtime_error(
            "GPIO lines have not been initialised"
        );
    }

    // Use libgpiod to change the GPIO output.
    lines_->set_value(
        static_cast<unsigned int>(relay.gpio_),
        on ? gpiod::line::value::ACTIVE
           : gpiod::line::value::INACTIVE
    );

    // Only update our stored state after the GPIO call
    // succeeds without throwing an exception.
    relay.isOn_ = on;
}


void RelayController::AllOff()
{
    if (!lines_) {
        return;
    }

    // Set all three requested GPIO outputs to INACTIVE.
    lines_->set_values(gpiod::line::value_mappings{
        {static_cast<unsigned int>(heatergpio_),
         gpiod::line::value::INACTIVE},

        {static_cast<unsigned int>(humidifiergpio_),
         gpiod::line::value::INACTIVE},

        {static_cast<unsigned int>(fangpio_),
         gpiod::line::value::INACTIVE}
    });

    Heater.isOn_ = false;
    Humidifier.isOn_ = false;
    Fan.isOn_ = false;
}


RelayController::~RelayController() noexcept
{
    try {
        AllOff();
    }
    catch (const std::exception& e) {
        std::cerr << "Failed to turn relays OFF: "
                  << e.what() << '\n';
    }
}
