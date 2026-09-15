// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <vector>

namespace {
struct HostTime { double adc, current, dac; };
struct CaptureInfo { std::uint64_t position, frames, flags; double adc; };
class Input {
public:
    Input(std::size_t slots, std::size_t frames, std::size_t channels, double rate)
        : slots_(slots), frames_(frames), channels_(channels), rate_(rate),
          audio_(slots * frames * channels), info_(slots) {}
    void capture(const float* source, std::size_t frames, const HostTime* time,
                 unsigned long flags) noexcept {
        for (std::size_t offset = 0; offset < frames;) {
            const auto count = std::min(frames_, frames - offset);
            const auto w = write_.load(std::memory_order_relaxed);
            const auto r = read_.load(std::memory_order_acquire);
            if (w - r == slots_) {
                dropped_.fetch_add(count, std::memory_order_relaxed);
            } else {
                auto* target = audio_.data() + (w % slots_) * frames_ * channels_;
                if (source) std::memcpy(target, source + offset * channels_, count * channels_ * sizeof(float));
                else std::fill(target, target + count * channels_, 0.0f);
                info_[w % slots_] = {position_, count, flags,
                    time ? time->adc + offset / rate_ : -1.0};
                write_.store(w + 1, std::memory_order_release);
            }
            position_ += count;
            offset += count;
        }
        total_.store(position_, std::memory_order_release);
    }
    int read(float* target, std::size_t capacity, CaptureInfo* info) noexcept {
        const auto r = read_.load(std::memory_order_relaxed);
        if (r == write_.load(std::memory_order_acquire)) return 0;
        const auto item = info_[r % slots_];
        if (capacity < item.frames) return -1;
        std::memcpy(target, audio_.data() + (r % slots_) * frames_ * channels_,
                    item.frames * channels_ * sizeof(float));
        *info = item;
        read_.store(r + 1, std::memory_order_release);
        return 1;
    }
    std::uint64_t total() const noexcept { return total_.load(std::memory_order_acquire); }
    std::uint64_t dropped() const noexcept { return dropped_.load(std::memory_order_relaxed); }
private:
    std::size_t slots_, frames_, channels_;
    double rate_;
    std::vector<float> audio_;
    std::vector<CaptureInfo> info_;
    std::uint64_t position_{0};
    alignas(64) std::atomic<std::uint64_t> write_{0}, total_{0}, dropped_{0};
    alignas(64) std::atomic<std::uint64_t> read_{0};
};
}

ANH_API void* anh_input_create(std::size_t slots, std::size_t frames,
                              std::size_t channels, double rate) noexcept {
    if (slots < 2 || slots > 1024 || !frames || frames > 65536 ||
        !channels || channels > 64 || !std::isfinite(rate) || rate <= 0 ||
        slots * frames * channels > 16777216) return nullptr;
    try { return new Input(slots, frames, channels, rate); } catch (...) { return nullptr; }
}
ANH_API void anh_input_destroy(void* handle) noexcept { delete static_cast<Input*>(handle); }
ANH_API int anh_input_read(void* handle, float* output, std::size_t capacity, void* info) noexcept {
    if (!handle || !output || !info) return -1;
    return static_cast<Input*>(handle)->read(output, capacity, static_cast<CaptureInfo*>(info));
}
ANH_API std::uint64_t anh_input_total(void* handle) noexcept {
    return handle ? static_cast<Input*>(handle)->total() : 0;
}
ANH_API std::uint64_t anh_input_dropped(void* handle) noexcept {
    return handle ? static_cast<Input*>(handle)->dropped() : 0;
}
ANH_API int anh_input_callback(const void* input, void*, unsigned long frames,
                              const void* time, unsigned long flags, void* handle) noexcept {
    if (!handle) return 2;
    static_cast<Input*>(handle)->capture(static_cast<const float*>(input), frames,
                                         static_cast<const HostTime*>(time), flags);
    return 0;
}
