cmake_minimum_required(VERSION 3.20)
project(anharmonic_audio LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Production code is identical on Windows, Linux and both macOS architectures.
# Device APIs belong to host adapters, never the portable render target.
set(CORE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../mpclab/native)
add_library(anharmonic_audio SHARED
    ${CORE_DIR}/synth.cpp
    ${CORE_DIR}/sample.cpp
    ${CORE_DIR}/mixer.cpp
    ${CORE_DIR}/convolution.cpp
    ${CORE_DIR}/sends.cpp
    ${CORE_DIR}/output.cpp
)
target_include_directories(anharmonic_audio PUBLIC ${CORE_DIR})
if(MSVC)
    target_compile_options(anharmonic_audio PRIVATE /W4 /fp:strict)
else()
    target_compile_options(anharmonic_audio PRIVATE -Wall -Wextra -Wpedantic -fno-fast-math -ffp-contract=off)
endif()
install(TARGETS anharmonic_audio LIBRARY DESTINATION lib RUNTIME DESTINATION bin)

include(CTest)
if(BUILD_TESTING)
    find_package(Threads REQUIRED)
    add_executable(native_core_test tests/core_test.cpp)
    target_link_libraries(native_core_test PRIVATE anharmonic_audio Threads::Threads)
    add_test(NAME portable_core COMMAND native_core_test)
endif()

# The historical ALSA/LV2 prototype is explicitly opt-in. It is not used by
# production binaries and must never impose Linux dependencies on their build.
option(ANHARMONIC_BUILD_LEGACY_LV2 "Build the Linux-only LV2 prototype" OFF)
if(ANHARMONIC_BUILD_LEGACY_LV2)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "The legacy ALSA/LV2 prototype is Linux-only")
endif()

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
endif()
