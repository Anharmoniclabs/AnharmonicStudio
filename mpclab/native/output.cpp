// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <vector>

namespace {
static_assert(std::atomic<std::uint64_t>::is_always_lock_free,
              "Supported release architectures require lock-free 64-bit counters");
class Output {
public:
    explicit Output(std::size_t capacity) : capacity_(capacity), audio_(capacity * 2) {}
    bool write(const float* source, std::size_t frames) noexcept {
        const auto write = write_.load(std::memory_order_relaxed);
        const auto read = read_.load(std::memory_order_acquire);
        if (frames > capacity_ || write - read > capacity_ - frames) return false;
        copy_in(source, write % capacity_, frames);
        write_.store(write + frames, std::memory_order_release);
        return true;
    }
    void read(float* destination, std::size_t frames, unsigned long flags) noexcept {
        const auto read = read_.load(std::memory_order_relaxed);
        const auto write = write_.load(std::memory_order_acquire);
        const auto count = std::min<std::uint64_t>(write - read, frames);
        const auto start = read % capacity_;
        const auto first = std::min<std::uint64_t>(count, capacity_ - start);
        std::memcpy(destination, audio_.data() + start * 2, first * 2 * sizeof(float));
        std::memcpy(destination + first * 2, audio_.data(), (count - first) * 2 * sizeof(float));
        if (count < frames) std::fill(destination + count * 2, destination + frames * 2, 0.0f);
        // Never wait for a producer or replay stale audio on starvation.
        if (count < frames || (flags & 4UL)) underruns_.fetch_add(1, std::memory_order_relaxed);
        read_.store(read + count, std::memory_order_release);
    }
    std::uint64_t available() const noexcept {
        // Load reader first: concurrently advancing the producer cannot make
        // occupancy wrap below zero. This value is advisory outside its owner.
        const auto read = read_.load(std::memory_order_acquire);
        const auto write = write_.load(std::memory_order_acquire);
        return std::min<std::uint64_t>(capacity_, write - read);
    }
    std::uint64_t underruns() const noexcept { return underruns_.load(std::memory_order_relaxed); }
    void reset_stats() noexcept { underruns_.store(0, std::memory_order_relaxed); }
private:
    void copy_in(const float* source, std::size_t start, std::size_t frames) noexcept {
        const auto first = std::min(frames, capacity_ - start);
        std::memcpy(audio_.data() + start * 2, source, first * 2 * sizeof(float));
        std::memcpy(audio_.data(), source + first * 2, (frames - first) * 2 * sizeof(float));
    }
    std::size_t capacity_;
    std::vector<float> audio_;
    alignas(64) std::atomic<std::uint64_t> write_{0};
    alignas(64) std::atomic<std::uint64_t> read_{0};
    alignas(64) std::atomic<std::uint64_t> underruns_{0};
};
}

ANH_API void* anh_output_create(std::size_t frames) noexcept {
    if (!frames || frames > 65536) return nullptr;
    try { return new Output(frames); } catch (...) { return nullptr; }
}
ANH_API void anh_output_destroy(void* handle) noexcept { delete static_cast<Output*>(handle); }
ANH_API int anh_output_write(void* handle, const float* audio, std::size_t frames) noexcept {
    if (!handle || !audio) return -1;
    return static_cast<Output*>(handle)->write(audio, frames) ? 0 : 1;
}
ANH_API std::uint64_t anh_output_available(void* handle) noexcept {
    return handle ? static_cast<Output*>(handle)->available() : 0;
}
ANH_API std::uint64_t anh_output_underruns(void* handle) noexcept {
    return handle ? static_cast<Output*>(handle)->underruns() : 0;
}
ANH_API void anh_output_reset_stats(void* handle) noexcept {
    if (handle) static_cast<Output*>(handle)->reset_stats();
}
ANH_API int anh_output_callback(const void*, void* output, unsigned long frames,
                               const void*, unsigned long flags, void* userdata) noexcept {
    if (!userdata || !output) return 2; // paAbort
    static_cast<Output*>(userdata)->read(static_cast<float*>(output), frames, flags);
    return 0; // paContinue
}
