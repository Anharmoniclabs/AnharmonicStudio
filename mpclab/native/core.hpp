// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstddef>
#include <cstdint>

#if defined(_WIN32)
#define ANH_API extern "C" __declspec(dllexport)
#else
#define ANH_API extern "C" __attribute__((visibility("default")))
#endif

// All buffers are borrowed for the duration of a call. No renderer allocates,
// locks, performs I/O, or invokes the Python runtime. Sizes use fixed-width
// fields across Windows LLP64, Linux LP64 and both macOS architectures.
struct AnhSampleVoice {
    std::int64_t start, end, attack, release, length, age, crossfade;
    double rate, gain, left, right;
    std::int32_t loop, offline, dead;
};

ANH_API int anh_core_abi() noexcept;
ANH_API std::size_t anh_sample_voice_size() noexcept;
ANH_API int anh_sample_render(const float* source, std::int64_t source_frames,
                             float* output, std::int64_t frames,
                             AnhSampleVoice* voice) noexcept;
ANH_API void anh_limit(float* audio, std::size_t frames, double ceiling,
                      double release_step, double* state) noexcept;
ANH_API void anh_compress(float* audio, std::size_t frames, double threshold,
                         double ratio, double attack_b, double release_b,
                         double makeup, float* slow, float* fast,
                         double* reduction) noexcept;
ANH_API void anh_saturate(float* audio, std::size_t samples, double drive) noexcept;
ANH_API void anh_mix_meter(const float* source, float* output, std::size_t frames,
                          const double* left, const double* right, int automated,
                          double* meter) noexcept;

ANH_API void* anh_convolver_create(const float* ir, std::size_t taps,
                                  std::size_t channels, std::size_t max_frames) noexcept;
ANH_API void anh_convolver_destroy(void* handle) noexcept;
ANH_API void anh_convolver_reset(void* handle) noexcept;
ANH_API int anh_convolver_process(void* handle, float* audio, std::size_t frames) noexcept;
ANH_API int anh_convolver_transfer(void* source, void* destination) noexcept;

ANH_API void* anh_reverb_create(std::size_t max_frames) noexcept;
ANH_API void anh_reverb_destroy(void* handle) noexcept;
ANH_API void anh_reverb_reset(void* handle) noexcept;
ANH_API int anh_reverb_process(void* handle, const float* input, float* output,
                              std::size_t frames, const double* params) noexcept;
ANH_API void* anh_delay_create(std::size_t max_frames) noexcept;
ANH_API void anh_delay_destroy(void* handle) noexcept;
ANH_API void anh_delay_reset(void* handle) noexcept;
ANH_API int anh_delay_process(void* handle, const float* input, float* output,
                             std::size_t frames, const double* params) noexcept;
