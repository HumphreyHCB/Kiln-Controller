
cmake_minimum_required(VERSION 3.16)

project(TobaccoKilnController)

add_executable(kiln
    src/KilnController.cpp
    src/TemperatureHumidityCollector.cpp
    src/KilnControlLogic.cpp
    src/RelayController.cpp
)

find_package(sdbus-c++ REQUIRED)

find_package(PkgConfig REQUIRED)
pkg_check_modules(GPIOD REQUIRED IMPORTED_TARGET libgpiodcxx)

target_link_libraries(kiln PRIVATE PkgConfig::GPIOD)

target_link_libraries(kiln PRIVATE SDBusCpp::sdbus-c++)