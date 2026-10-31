#include <iostream>
#include "TemperatureHumidityCollector.hpp"
#include "KilnControlLogic.hpp"
#include "RelayController.hpp"
#include <thread>

using namespace std;

int main()
{
    cout << "\nStarting Kiln Controller\n";
    std::cout << R"(
                  /\
                 /  \
               .' /\ '.
             .'  /  \  '.
            /   / /\ \   \
           /   / /  \ \   \
          /   / / || \ \   \
         |   / /  ||  \ \   |
         |  / /---||---\ \  |
         |  \ \   ||   / /  |
          \  \ \  ||  / /  /
           \  \ \ || / /  /
            '. \ \||/ / .'
              '-._||_.-'
                  ||
                  ||
                  ||
    )" << '\n';

    std::cout << "Tobacco Kiln Controller starting...\n";
    std::cout << "Collecting Temperature & Humidity ...\n";
    try
    {
        std::cout << "Tobacco Kiln Controller starting...\n";

        std::cout << "Monitoring started.\n";
        TemperatureHumidityCollector collector;
        RelayController relays;
        KilnControlLogic logic;

        // Start Bluetooth listening in the background
        collector.start();
        while (true)
        {
            

            auto reading = collector.getLatestReading();

            // Display measurement if available
            if (reading)
            {
                auto age = std::chrono::duration_cast<
                               std::chrono::seconds>(
                               std::chrono::steady_clock::now() - reading->receivedAt)
                               .count();

                std::cout << "Temperature: "
                          << reading->temperature << " °C\n";

                std::cout << "Humidity: "
                          << reading->humidity << " %\n";

                std::cout << "Reading age: " << age << " seconds\n";
            }
            else
            {
                std::cout << "No reading received yet.\n";
            }

            // Always request a decision, even without a reading
            auto decision = logic.decide(
                reading,
                relays.Heater.IsOn(),
                relays.Humidifier.IsOn());

            // Sensor fault: command all outputs OFF
            if (decision.sensorFault)
            {
                std::cerr << "Sensor fault: shutting down relays!\n";
                relays.AllOff();
                continue;
            }

            // Apply the heater decision
            switch (decision.heater)
            {
            case ControlAction::TurnOn:
                relays.Heater.On();
                break;

            case ControlAction::TurnOff:
                relays.Heater.Off();
                break;

            case ControlAction::LeaveAsIs:
                break;
            }

            // Apply the humidifier decision
            switch (decision.humidifier)
            {
            case ControlAction::TurnOn:
                relays.Humidifier.On();
                break;

            case ControlAction::TurnOff:
                relays.Humidifier.Off();
                break;

            case ControlAction::LeaveAsIs:
                break;
            }

            std::cout << "Heater ON: "
                      << std::boolalpha << relays.Heater.IsOn() << '\n';

            std::cout << "Humidifier ON: "
                      << relays.Humidifier.IsOn() << '\n';

            std::this_thread::sleep_for(std::chrono::seconds(5));
        }

        relays.AllOff();
        collector.stop();

        std::cout << "Controller stopped.\n";

        return 0;
    }
    catch (const std::exception &e)
    {
        std::cerr << "Controller error: "
                  << e.what() << '\n';
        return 1;
    }
}