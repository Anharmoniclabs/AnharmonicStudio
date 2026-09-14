// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <cmath>
#include <complex>
#include <utility>
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
        for (std::size_t i = 0; i < taps; ++i) finite_ir_ = finite_ir_ && std::isfinite(ir[i]);
        for (std::size_t i = 0; i < size_; ++i)
            roots_[i] = std::polar(1.0, -2.0 * pi * static_cast<double>(i) / size_);
        for (std::size_t n = power2(taps); n <= size_; n *= 2) {
            Plan plan{n, std::vector<Complex>(n), {}};
            // Every supported transform size is prepared off the render worker.
            // Reuse its permutation instead of rebuilding it for both FFTs in
            // every track's audio block.
            for (std::size_t i = 1, j = 0; i < n; ++i) {
                std::size_t bit = n >> 1;
                for (; j & bit; bit >>= 1) j ^= bit;
                j ^= bit;
                if (i < j) plan.swaps.emplace_back(i, j);
            }
            std::fill(scratch_.begin(), scratch_.end(), Complex{});
            for (std::size_t i = 0; i < taps; ++i) scratch_[i] = ir[i];
            if (finite_ir_) transform<true>(plan, false);
            else transform<false>(plan, false);
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
        bool finite = finite_ir_;
        for (std::size_t i = 0; i < frames; ++i) {
            scratch_[i] = Complex(audio[i * channels_], channels_ == 2 ? audio[i * channels_ + 1] : 0);
            finite = finite && std::isfinite(scratch_[i].real()) && std::isfinite(scratch_[i].imag());
        }
        if (finite) filter<true>(*plan);
        else filter<false>(*plan);
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
    struct Plan {
        std::size_t size;
        std::vector<Complex> spectrum;
        std::vector<std::pair<std::size_t, std::size_t>> swaps;
    };
    template<bool finite> static Complex multiply(Complex a, Complex b) noexcept {
        if constexpr (finite) {
            // Finite float32 source/IR values cannot overflow double at the
            // bounded transform sizes. Avoid complex infinity-recovery checks
            // in every butterfly, while retaining them for nonfinite input.
            return {a.real() * b.real() - a.imag() * b.imag(),
                    a.real() * b.imag() + a.imag() * b.real()};
        } else return a * b;
    }
    template<bool finite> void filter(const Plan& plan) noexcept {
        transform<finite>(plan, false);
        for (std::size_t i = 0; i < plan.size; ++i)
            scratch_[i] = multiply<finite>(scratch_[i], plan.spectrum[i]);
        transform<finite>(plan, true);
    }
    template<bool finite> void transform(const Plan& plan, bool inverse) noexcept {
        const auto size = plan.size;
        for (const auto& swap : plan.swaps) std::swap(scratch_[swap.first], scratch_[swap.second]);
        for (std::size_t length = 2; length <= size; length *= 2) {
            const auto half = length / 2;
            const auto stride = size_ / length;
            for (std::size_t base = 0; base < size; base += length) {
                for (std::size_t j = 0; j < half; ++j) {
                    const auto root = inverse ? std::conj(roots_[j * stride]) : roots_[j * stride];
                    const auto even = scratch_[base + j];
                    const auto odd = multiply<finite>(scratch_[base + j + half], root);
                    scratch_[base + j] = even + odd;
                    scratch_[base + j + half] = even - odd;
                }
            }
        }
        if (inverse) {
            // size is a power of two, so its reciprocal is exact in binary.
            // Avoid two scalar divisions per output bin without fast-math.
            const double scale = 1.0 / static_cast<double>(size);
            for (std::size_t i = 0; i < size; ++i) scratch_[i] *= scale;
        }
    }
    std::size_t taps_, channels_, frames_, size_;
    bool finite_ir_ = true;
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
