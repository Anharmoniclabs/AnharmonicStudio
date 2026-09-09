cmake_minimum_required(VERSION 3.20)
project(anharmonic_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(PkgConfig REQUIRED)
pkg_check_modules(PORTAUDIO REQUIRED IMPORTED_TARGET portaudio-2.0)
pkg_check_modules(ALSA REQUIRED IMPORTED_TARGET alsa)
pkg_check_modules(LILV REQUIRED IMPORTED_TARGET lilv-0)

add_library(anharmonic_native SHARED
    src/engine.cpp
    src/c_api.cpp
)

target_include_directories(anharmonic_native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(anharmonic_native PRIVATE
    PkgConfig::PORTAUDIO
    PkgConfig::ALSA
    PkgConfig::LILV
)

target_compile_options(anharmonic_native PRIVATE
    -Wall -Wextra -Wpedantic -Werror=return-type
)

set_target_properties(anharmonic_native PROPERTIES
    OUTPUT_NAME anharmonic_native
    VERSION 0.1.0
    SOVERSION 0
)

install(TARGETS anharmonic_native
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
)
