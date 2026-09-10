// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <cmath>
#include <limits>

namespace {
constexpr double pi = 3.14159265358979323846264338327950288;
double wrap(double value, double length) noexcept {
    double result = std::fmod(value, length);
    return result < 0 ? result + length : result;
}
double sinc(double x) noexcept {
    return x == 0 ? 1.0 : std::sin(pi * x) / (pi * x);
}
float sample(const float* data, double position, const AnhSampleVoice& v,
             int channel, bool loop) noexcept {
    auto index = static_cast<std::int64_t>(position);
    const auto span = v.end - v.start;
    if (v.offline && v.rate != 1.0) {
        index = static_cast<std::int64_t>(std::floor(position));
        const double cutoff = std::min(1.0, 1.0 / std::max(v.rate, 1e-12));
        double normal = 0, value = 0;
        for (int tap = -7; tap <= 8; ++tap) {
            auto at = index + tap;
            const double distance = position - static_cast<double>(at);
            const double weight = std::abs(distance) >= 8 ? 0 :
                cutoff * sinc(distance * cutoff) * sinc(distance / 8.0);
            if (loop) at = ((at - v.start) % span + span) % span + v.start;
            else at = std::clamp(at, v.start, v.end - 1);
            normal += weight;
            value += weight * data[at * 2 + channel];
        }
        return static_cast<float>(value / (std::abs(normal) > 1e-12 ? normal : 1));
    }
    index = std::clamp(index, v.start, v.end - 1);
    auto next = loop ? (index + 1 - v.start) % span + v.start :
                      std::min(index + 1, v.end - 1);
    // Float operations deliberately match the reference interpolation stages.
    const float fraction = static_cast<float>(position - static_cast<double>(index));
    const float first = data[index * 2 + channel];
    return first + (data[next * 2 + channel] - first) * fraction;
}
}

ANH_API int anh_core_abi() noexcept { return 1; }
ANH_API std::size_t anh_sample_voice_size() noexcept { return sizeof(AnhSampleVoice); }

ANH_API int anh_sample_render(const float* source, std::int64_t source_frames,
                             float* output, std::int64_t frames,
                             AnhSampleVoice* ptr) noexcept {
    if (!ptr || !source || !output || source_frames < 0 || frames < 0) return -1;
    auto& v = *ptr;
    if (v.start < 0 || v.end <= v.start || v.end > source_frames || v.age < 0 ||
        v.attack < 1 || v.release < 1 || v.length < 0 || !std::isfinite(v.rate) ||
        v.rate <= 0 || !std::isfinite(v.gain) || !std::isfinite(v.left) ||
        !std::isfinite(v.right)) return -1;
    if (v.dead || frames == 0) return 0;
    const auto span = v.end - v.start;
    const double source_length = std::max(1.0, std::ceil(span / std::max(v.rate, 1e-12)));
    const auto length = v.loop || source_length >= static_cast<double>(v.length) ?
                        v.length : static_cast<std::int64_t>(source_length);
    const auto count = std::min(frames, std::max<std::int64_t>(0, length - v.age));
    const auto fade = std::clamp(v.crossfade, std::int64_t{0}, std::max<std::int64_t>(0, span / 2 - 1));
    const bool sustain = v.age >= v.attack && v.age + count <= length - v.release;
    for (std::int64_t i = 0; i < count; ++i) {
        const auto age = v.age + i;
        const double travel = static_cast<double>(age) * v.rate;
        double position = v.start + (v.loop ? wrap(travel, span) : travel);
        double blend = 0;
        float alpha = 0;
        if (v.loop && fade > 0 && span > 2 * fade) {
            if (travel < span) {
                position = v.start + travel;
                blend = travel - (span - fade);
            } else {
                const double phase = wrap(travel - span, span - fade);
                position = v.start + fade + phase;
                blend = phase - (span - 2 * fade);
            }
            alpha = static_cast<float>(std::clamp(blend / fade, 0.0, 1.0));
            blend = v.start + wrap(blend, span);
        }
        const float envelope = sustain ? 1.0f : std::min(
            static_cast<float>(std::clamp(static_cast<double>(age) / v.attack, 0.0, 1.0)),
            static_cast<float>(std::clamp(static_cast<double>(length - age) / v.release, 0.0, 1.0)));
        for (int c = 0; c < 2; ++c) {
            float value = sample(source, position, v, c, v.loop != 0);
            if (alpha > 0) value = value * (1.0f - alpha) + sample(source, blend, v, c, true) * alpha;
            const float scale = envelope * static_cast<float>(v.gain * (c ? v.right : v.left));
            output[i * 2 + c] += value * scale;
        }
    }
    v.age += count;
    v.dead = v.age >= length;
    return 0;
}
