// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <cmath>
#include <complex>
#include <vector>

namespace {
using Complex = std::complex<double>;
constexpr double pi = 3.14159265358979323846264338327950288;
std::size_t power2(std::size_t value) noexcept {
    std::size_t size = 1;
    while (size < value) size *= 2;
    return size;
}

class Convolver {
public:
    Convolver(const float* ir, std::size_t taps, std::size_t channels, std::size_t frames)
        : taps_(taps), channels_(channels), frames_(frames), size_(power2(taps + frames - 1)),
          roots_(size_), scratch_(size_), tail_((taps - 1) * channels), next_(tail_.size()) {
        for (std::size_t i = 0; i < size_; ++i)
            roots_[i] = std::polar(1.0, -2.0 * pi * static_cast<double>(i) / size_);
        for (std::size_t n = power2(taps); n <= size_; n *= 2) {
            Plan plan{n, std::vector<Complex>(n)};
            std::fill(scratch_.begin(), scratch_.end(), Complex{});
            for (std::size_t i = 0; i < taps; ++i) scratch_[i] = ir[i];
            transform(n, false);
            std::copy_n(scratch_.begin(), n, plan.spectrum.begin());
            plans_.push_back(std::move(plan));
        }
    }
    void reset() noexcept { std::fill(tail_.begin(), tail_.end(), 0); }
    bool transfer(Convolver& destination) const noexcept {
        if (taps_ != destination.taps_ || channels_ != destination.channels_) return false;
        std::copy(tail_.begin(), tail_.end(), destination.tail_.begin());
        return true;
    }
    bool process(float* audio, std::size_t frames) noexcept {
        if (frames > frames_) return false;
        if (!frames) return true;
        const auto size = power2(frames + taps_ - 1);
        const Plan* plan = nullptr;
        for (const auto& candidate : plans_) if (candidate.size == size) { plan = &candidate; break; }
        if (!plan) return false;
        // A real impulse filters the real and imaginary components
        // independently. Pack stereo into one complex transform pair.
        std::fill_n(scratch_.begin(), size, Complex{});
        for (std::size_t i = 0; i < frames; ++i)
            scratch_[i] = Complex(audio[i * channels_], channels_ == 2 ? audio[i * channels_ + 1] : 0);
        transform(size, false);
        for (std::size_t i = 0; i < size; ++i) scratch_[i] *= plan->spectrum[i];
        transform(size, true);
        for (std::size_t c = 0; c < channels_; ++c) {
            for (std::size_t i = 0; i < frames; ++i) {
                const double overlap = i < taps_ - 1 ? tail_[i * channels_ + c] : 0;
                const double value = c ? scratch_[i].imag() : scratch_[i].real();
                audio[i * channels_ + c] = static_cast<float>(value + overlap);
            }
            for (std::size_t i = 0; i < taps_ - 1; ++i) {
                float value = static_cast<float>(c ? scratch_[frames + i].imag() : scratch_[frames + i].real());
                if (frames + i < taps_ - 1) value += tail_[(frames + i) * channels_ + c];
                next_[i * channels_ + c] = value;
            }
        }
        tail_.swap(next_);
        return true;
    }
private:
    void transform(std::size_t size, bool inverse) noexcept {
        for (std::size_t i = 1, j = 0; i < size; ++i) {
            std::size_t bit = size >> 1;
            for (; j & bit; bit >>= 1) j ^= bit;
            j ^= bit;
            if (i < j) std::swap(scratch_[i], scratch_[j]);
        }
        for (std::size_t length = 2; length <= size; length *= 2) {
            const auto half = length / 2;
            const auto stride = size_ / length;
            for (std::size_t base = 0; base < size; base += length) {
                for (std::size_t j = 0; j < half; ++j) {
                    const auto root = inverse ? std::conj(roots_[j * stride]) : roots_[j * stride];
                    const auto even = scratch_[base + j];
                    const auto odd = scratch_[base + j + half] * root;
                    scratch_[base + j] = even + odd;
                    scratch_[base + j + half] = even - odd;
                }
            }
        }
        if (inverse) for (std::size_t i = 0; i < size; ++i) scratch_[i] /= static_cast<double>(size);
    }
    struct Plan { std::size_t size; std::vector<Complex> spectrum; };
    std::size_t taps_, channels_, frames_, size_;
    std::vector<Complex> roots_, scratch_;
    std::vector<float> tail_, next_;
    std::vector<Plan> plans_;
};
}

ANH_API void* anh_convolver_create(const float* ir, std::size_t taps,
                                  std::size_t channels, std::size_t max_frames) noexcept {
    if (!ir || !taps || taps > 65536 || !channels || channels > 2 ||
        !max_frames || max_frames > 16384) return nullptr;
    try { return new Convolver(ir, taps, channels, max_frames); }
    catch (...) { return nullptr; }
}
ANH_API void anh_convolver_destroy(void* handle) noexcept { delete static_cast<Convolver*>(handle); }
ANH_API void anh_convolver_reset(void* handle) noexcept {
    if (handle) static_cast<Convolver*>(handle)->reset();
}
ANH_API int anh_convolver_process(void* handle, float* audio, std::size_t frames) noexcept {
    if (!handle || !audio) return -1;
    return static_cast<Convolver*>(handle)->process(audio, frames) ? 0 : -1;
}
ANH_API int anh_convolver_transfer(void* source, void* destination) noexcept {
    if (!source || !destination) return -1;
    return static_cast<Convolver*>(source)->transfer(*static_cast<Convolver*>(destination)) ? 0 : -1;
}
