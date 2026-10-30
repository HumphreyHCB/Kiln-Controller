# include <iostream>
# include "TemperatureHumidityCollector.hpp"
# include "KilnControlLogic.hpp"

using namespace std;

int main(){
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
     try {
        std::cout << "Tobacco Kiln Controller starting...\n";

        TemperatureHumidityCollector collector;

        // Start Bluetooth listening in the background
        collector.start();

        std::cout << "Monitoring started.\n";

        while (true) {
            std::cout << "\nPress Enter for latest reading, "
                         "or q to quit: ";

            std::string command;

            if (!std::getline(std::cin, command)
                || command == "q") {
                break;
            }

            auto reading = collector.getLatestReading();

            if (!reading) {
                std::cout << "No reading received yet.\n";
                continue;
            }

            // How many seconds since this reading arrived?
            auto age = std::chrono::duration_cast<
                std::chrono::seconds
            >(
                std::chrono::steady_clock::now()
                - reading->receivedAt
            ).count();

            std::cout << "Temperature: "
                      << reading->temperature << " °C\n";

            std::cout << "Humidity: "
                      << reading->humidity << " %\n";

            std::cout << "Reading age: "
                      << age << " seconds\n";

            KilnControlLogic logic;

            bool heaterIsOn = false;
            bool humidifierIsOn = false;
            
            auto decision = logic.decide(
                collector.getLatestReading(),
                heaterIsOn,
                humidifierIsOn
            );
             std::cout << "Decision \n";
            std::cout << "Heater should : "<< static_cast<int>(decision.heater) << "\n";
            std::cout << "Humidifier should : "<< static_cast<int>(decision.humidifier) << "\n";

            if (age > 60) {
                std::cout << "WARNING: Reading is stale!\n";
            }
        }

        collector.stop();

        std::cout << "Controller stopped.\n";

        return 0;
    }
    catch (const std::exception& e) {
        std::cerr << "Controller error: "
                  << e.what() << '\n';
        return 1;
    }
}