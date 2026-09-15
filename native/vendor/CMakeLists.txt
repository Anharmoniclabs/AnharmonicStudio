# Pinned Rubber Band 4.0.0, GPL-2.0-or-later. Upstream source is unmodified.
set(RB ${CMAKE_CURRENT_SOURCE_DIR}/rubberband)
add_library(rubberband SHARED
    ${RB}/src/rubberband-c.cpp
    ${RB}/src/RubberBandStretcher.cpp
    ${RB}/src/RubberBandLiveShifter.cpp
    ${RB}/src/faster/AudioCurveCalculator.cpp
    ${RB}/src/faster/CompoundAudioCurve.cpp
    ${RB}/src/faster/HighFrequencyAudioCurve.cpp
    ${RB}/src/faster/SilentAudioCurve.cpp
    ${RB}/src/faster/PercussiveAudioCurve.cpp
    ${RB}/src/faster/R2Stretcher.cpp
    ${RB}/src/faster/StretcherChannelData.cpp
    ${RB}/src/faster/StretcherProcess.cpp
    ${RB}/src/common/Allocators.cpp
    ${RB}/src/common/FFT.cpp
    ${RB}/src/common/Log.cpp
    ${RB}/src/common/Profiler.cpp
    ${RB}/src/common/Resampler.cpp
    ${RB}/src/common/BQResampler.cpp
    ${RB}/src/common/StretchCalculator.cpp
    ${RB}/src/common/sysutils.cpp
    ${RB}/src/common/mathmisc.cpp
    ${RB}/src/common/Thread.cpp
    ${RB}/src/finer/R3Stretcher.cpp
    ${RB}/src/finer/R3LiveShifter.cpp)
target_include_directories(rubberband PRIVATE ${RB})
target_compile_definitions(rubberband PRIVATE USE_BUILTIN_FFT USE_BQRESAMPLER NO_THREAD_CHECKS NO_TIMING NDEBUG)
set_target_properties(rubberband PROPERTIES VERSION 3.0.0 SOVERSION 3)
if(WIN32)
    target_compile_definitions(rubberband PRIVATE NOMINMAX _USE_MATH_DEFINES)
else()
    find_package(Threads REQUIRED)
    target_link_libraries(rubberband PRIVATE Threads::Threads)
    target_compile_definitions(rubberband PRIVATE USE_PTHREADS HAVE_POSIX_MEMALIGN)
    if(APPLE)
        target_compile_definitions(rubberband PRIVATE LACK_SINCOS)
    endif()
endif()
install(TARGETS rubberband LIBRARY DESTINATION lib RUNTIME DESTINATION bin)
