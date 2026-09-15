cmake_minimum_required(VERSION 3.22)
project(AnharmonicPrism VERSION 1.1.0 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(JUCE_SOURCE_DIR "" CACHE PATH "Pinned JUCE 7.0.12 source directory")
if(NOT EXISTS "${JUCE_SOURCE_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "Run scripts/build_prism.py or set JUCE_SOURCE_DIR to JUCE 7.0.12")
endif()
add_subdirectory("${JUCE_SOURCE_DIR}" juce EXCLUDE_FROM_ALL)
set(PRISM_FORMATS VST3 Standalone)
if(APPLE)
    list(APPEND PRISM_FORMATS AU)
endif()
juce_add_plugin(AnharmonicPrism
    COMPANY_NAME "Anharmonic Labs" BUNDLE_ID "com.anharmoniclabs.prism"
    PLUGIN_MANUFACTURER_CODE Ahml PLUGIN_CODE Ahpr
    PRODUCT_NAME "Anharmonic Prism" FORMATS ${PRISM_FORMATS}
    IS_SYNTH TRUE NEEDS_MIDI_INPUT TRUE NEEDS_MIDI_OUTPUT FALSE
    IS_MIDI_EFFECT FALSE EDITOR_WANTS_KEYBOARD_FOCUS FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE VST3_CATEGORIES Instrument Synth)
target_sources(AnharmonicPrism PRIVATE src/Processor.cpp src/Editor.cpp ../../mpclab/native/synth.cpp)
target_compile_definitions(AnharmonicPrism PUBLIC JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0 JUCE_DISPLAY_SPLASH_SCREEN=0 JUCE_REPORT_APP_USAGE=0)
target_link_libraries(AnharmonicPrism PRIVATE juce::juce_audio_utils juce::juce_dsp
    PUBLIC juce::juce_recommended_config_flags)
include(CTest)
if(BUILD_TESTING)
    add_executable(prism_core_test tests/core.cpp ../../mpclab/native/synth.cpp)
    target_include_directories(prism_core_test PRIVATE src)
    add_test(NAME prism_core COMMAND prism_core_test)
endif()
if(BUILD_TESTING)
    add_executable(prism_processor_test tests/processor.cpp)
    target_include_directories(prism_processor_test PRIVATE src)
    target_link_libraries(prism_processor_test PRIVATE AnharmonicPrism juce::juce_audio_utils juce::juce_dsp)
    add_test(NAME prism_processor COMMAND prism_processor_test)
endif()
