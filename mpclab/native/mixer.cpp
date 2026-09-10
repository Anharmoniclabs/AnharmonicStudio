// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <cmath>

ANH_API void anh_limit(float* audio, std::size_t frames, double ceiling,
                      double step, double* state) noexcept {
    float lowest = 1.0f;
    // Preserve the established float32 vector recurrence, including its
    // rounding, while eliminating temporary arrays and repeated NumPy calls.
    float running = 1.0f;
    const float bound = static_cast<float>(state[0] + step);
    const float release = static_cast<float>(step);
    float last = static_cast<float>(state[0]);
    for (std::size_t i = 0; i < frames; ++i) {
        auto* frame = audio + i * 2;
        for (int c = 0; c < 2; ++c) if (!std::isfinite(frame[c])) frame[c] = 0;
        const float peak = std::max(std::abs(frame[0]), std::abs(frame[1]));
        const float target = peak > ceiling ? static_cast<float>(ceiling) / peak : 1.0f;
        const float ramp = static_cast<float>(i) * release;
        running = std::min(running, target - ramp);
        last = std::min(1.0f, std::min(running, bound) + ramp);
        lowest = std::min(lowest, last);
        frame[0] *= last; frame[1] *= last;
    }
    if (frames) { state[0] = last; state[1] = -20.0 * std::log10(std::max(lowest, 1e-9f)); }
}

ANH_API void anh_compress(float* audio, std::size_t frames, double threshold,
                         double ratio, double attack_b, double release_b,
                         double makeup, float* slow, float* fast,
                         double* reduction) noexcept {
    double held_state = *slow, env_state = *fast;
    float maximum = 0;
    const float slope = static_cast<float>(1.0 - 1.0 / std::max(1.0, ratio));
    for (std::size_t i = 0; i < frames; ++i) {
        auto* frame = audio + i * 2;
        const float rect = std::max(std::abs(frame[0]), std::abs(frame[1]));
        held_state = (1.0 - release_b) * rect + release_b * held_state;
        const float held = std::max(rect, static_cast<float>(held_state));
        env_state = (1.0 - attack_b) * held + attack_b * env_state;
        const float level = 20.0f * std::log10(std::max(static_cast<float>(env_state), 1e-7f));
        const float over = level - static_cast<float>(threshold);
        const float gr = over <= -3 ? 0 : over >= 3 ? slope * over :
                         slope * (over + 3) * (over + 3) / 12.0f;
        maximum = std::max(maximum, gr);
        const float gain = std::pow(10.0f, (static_cast<float>(makeup) - gr) / 20.0f);
        frame[0] *= gain; frame[1] *= gain;
    }
    *slow = static_cast<float>(held_state); *fast = static_cast<float>(env_state);
    *reduction = maximum;
}

ANH_API void anh_saturate(float* audio, std::size_t samples, double drive) noexcept {
    if (drive <= 1e-4) return;
    const float pre = static_cast<float>(1.0 + drive * 11.0);
    const float gain = static_cast<float>((1.0 - 0.45 * drive) / std::tanh(1.0 + drive * 11.0));
    for (std::size_t i = 0; i < samples; ++i) audio[i] = std::tanh(audio[i] * pre) * gain;
}

ANH_API void anh_mix_meter(const float* source, float* output, std::size_t frames,
                          const double* left, const double* right, int automated,
                          double* meter) noexcept {
    double square = 0;
    float peak = 0;
    for (std::size_t i = 0; i < frames; ++i) {
        const auto gain_index = automated ? i : 0;
        const float l = static_cast<float>(source[i * 2] * left[gain_index]);
        const float r = static_cast<float>(source[i * 2 + 1] * right[gain_index]);
        output[i * 2] = l; output[i * 2 + 1] = r;
        square += static_cast<double>(l * l) + static_cast<double>(r * r);
        peak = std::max(peak, std::max(std::abs(l), std::abs(r)));
    }
    meter[0] = frames ? std::sqrt(square / (2 * frames)) : 0;
    meter[1] = peak;
}
