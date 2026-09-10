// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <array>
#include <cstdint>
#include <vector>

namespace {
constexpr int rate = 48000;
class Line {
public:
    explicit Line(std::size_t size) : size_(size), data_(size * 2) {}
    void reset() noexcept { std::fill(data_.begin(), data_.end(), 0); position_ = 0; }
    float read(std::size_t offset, std::size_t delay, int channel) const noexcept {
        const auto at = (position_ + offset + size_ - std::min(delay, size_ - 1)) % size_;
        return data_[at * 2 + channel];
    }
    void write(std::size_t offset, int channel, float value) noexcept {
        data_[(position_ + offset) % size_ * 2 + channel] = value;
    }
    void advance(std::size_t count) noexcept { position_ = (position_ + count) % size_; }
private:
    std::size_t size_, position_{0};
    std::vector<float> data_;
};

class Reverb {
public:
    explicit Reverb(std::size_t frames) : capacity_(frames), source_(frames * 2), scratch_(frames * 2),
        predelay_(rate / 4),
        combs_{{Line(1243), Line(1322), Line(1419), Line(1505), Line(1577), Line(1652), Line(1724), Line(1789)}},
        allpass_{{Line(609), Line(484), Line(375), Line(249)}} {}
    void reset() noexcept {
        predelay_.reset();
        for (auto& line : combs_) line.reset();
        for (auto& line : allpass_) line.reset();
        for (auto& state : damps_) state.fill(0);
    }
    bool process(const float* input, float* out, std::size_t n, const double* p) noexcept {
        if (n > capacity_) return false;
        std::fill_n(out, n * 2, 0);
        const auto pre = static_cast<std::size_t>(std::max(1.0, p[0] * rate));
        const float feedback = static_cast<float>(0.70 + 0.28 * std::clamp(p[1], 0.0, 1.0));
        const double damping = 0.05 + 0.9 * std::clamp(p[2], 0.0, 1.0);
        for (std::size_t i = 0; i < n; ++i) for (int c = 0; c < 2; ++c) predelay_.write(i, c, input[i * 2 + c]);
        predelay_.advance(n);
        for (std::size_t i = 0; i < n; ++i) for (int c = 0; c < 2; ++c)
            source_[i * 2 + c] = (pre > n ? predelay_.read(i, pre, c) : input[i * 2 + c]) * 0.11f;
        for (std::size_t line = 0; line < combs_.size(); ++line) {
            auto& ring = combs_[line];
            for (std::size_t i = 0; i < n; ++i) for (int c = 0; c < 2; ++c) {
                const float value = ring.read(i, delays_[line] + (c ? 25 : 0), c);
                scratch_[i * 2 + c] = value;
                out[i * 2 + c] += value;
            }
            for (int c = 0; c < 2; ++c) {
                double state = damps_[line][c];
                for (std::size_t i = 0; i < n; ++i) {
                    state = (1.0 - damping) * scratch_[i * 2 + c] + damping * state;
                    ring.write(i, c, source_[i * 2 + c] + static_cast<float>(state) * feedback);
                }
                damps_[line][c] = static_cast<float>(state);
            }
            ring.advance(n);
        }
        for (std::size_t line = 0; line < allpass_.size(); ++line) {
            auto& ring = allpass_[line];
            for (std::size_t i = 0; i < n; ++i) {
                for (int c = 0; c < 2; ++c) {
                    const float stored = ring.read(0, diffusion_[line], c);
                    const float value = out[i * 2 + c];
                    ring.write(0, c, value + stored * 0.5f);
                    out[i * 2 + c] = stored - value;
                }
                ring.advance(1);
            }
        }
        const float width = static_cast<float>(std::clamp(p[3], 0.0, 1.0));
        const float level = static_cast<float>(p[4]);
        for (std::size_t i = 0; i < n; ++i) {
            auto* frame = out + i * 2;
            if (width < 0.999f) {
                const float mid = (frame[0] + frame[1]) * 0.5f;
                frame[0] = mid + (frame[0] - mid) * width;
                frame[1] = mid + (frame[1] - mid) * width;
            }
            frame[0] *= level; frame[1] *= level;
        }
        return true;
    }
private:
    static constexpr std::array<std::size_t, 8> delays_{{1214,1293,1390,1476,1548,1623,1695,1760}};
    static constexpr std::array<std::size_t, 4> diffusion_{{605,480,371,245}};
    std::size_t capacity_;
    std::vector<float> source_, scratch_;
    Line predelay_;
    std::array<Line, 8> combs_;
    std::array<Line, 4> allpass_;
    std::array<std::array<float, 2>, 8> damps_{};
};

class Delay {
public:
    explicit Delay(std::size_t frames) : capacity_(frames), scratch_(frames * 2), line_(rate * 4) {}
    void reset() noexcept { line_.reset(); damp_.fill(0); }
    bool process(const float* input, float* output, std::size_t n, const double* p) noexcept {
        if (n > capacity_) return false;
        const auto delay = static_cast<std::size_t>(std::clamp(p[0], static_cast<double>(std::max<std::size_t>(64,n)), rate * 4.0 - 2));
        const double damping = 0.15 + 0.8 * p[1];
        const float feedback = static_cast<float>(std::clamp(p[2], 0.0, 0.95));
        for (int c = 0; c < 2; ++c) {
            double state = damp_[c];
            for (std::size_t i = 0; i < n; ++i) {
                const float value = line_.read(i, delay, c);
                output[i * 2 + c] = value * static_cast<float>(p[4]);
                state = (1.0 - damping) * value + damping * state;
                scratch_[i * 2 + c] = static_cast<float>(state) * feedback;
            }
            damp_[c] = static_cast<float>(state);
        }
        for (std::size_t i = 0; i < n; ++i) for (int c = 0; c < 2; ++c)
            line_.write(i,c,input[i * 2 + c] + scratch_[i * 2 + (p[3] != 0 ? 1-c : c)]);
        line_.advance(n);
        return true;
    }
private:
    std::size_t capacity_;
    std::vector<float> scratch_;
    Line line_;
    std::array<float, 2> damp_{};
};
}

ANH_API void* anh_reverb_create(std::size_t frames) noexcept {
    if (!frames || frames > 16384) return nullptr;
    try { return new Reverb(frames); } catch (...) { return nullptr; }
}
ANH_API void anh_reverb_destroy(void* handle) noexcept { delete static_cast<Reverb*>(handle); }
ANH_API void anh_reverb_reset(void* handle) noexcept { if (handle) static_cast<Reverb*>(handle)->reset(); }
ANH_API int anh_reverb_process(void* handle, const float* input, float* output, std::size_t n, const double* p) noexcept {
    if (!handle || !input || !output || !p) return -1;
    return static_cast<Reverb*>(handle)->process(input, output, n, p) ? 0 : -1;
}
ANH_API void* anh_delay_create(std::size_t frames) noexcept {
    if (!frames || frames > 16384) return nullptr;
    try { return new Delay(frames); } catch (...) { return nullptr; }
}
ANH_API void anh_delay_destroy(void* handle) noexcept { delete static_cast<Delay*>(handle); }
ANH_API void anh_delay_reset(void* handle) noexcept { if (handle) static_cast<Delay*>(handle)->reset(); }
ANH_API int anh_delay_process(void* handle, const float* input, float* output, std::size_t n, const double* p) noexcept {
    if (!handle || !input || !output || !p) return -1;
    return static_cast<Delay*>(handle)->process(input, output, n, p) ? 0 : -1;
}
