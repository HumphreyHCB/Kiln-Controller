
cmake_minimum_required(VERSION 3.16)

project(TobaccoKilnController)

add_executable(kiln
    src/KilnController.cpp
    src/TemperatureHumidityCollector.cpp
    src/KilnControlLogic.cpp
)

find_package(sdbus-c++ REQUIRED)

target_link_libraries(kiln PRIVATE SDBusCpp::sdbus-c++)