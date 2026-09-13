#pragma once

#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <vector>

namespace anharmonic {

// Cross-thread control handoff for an LV2 instance.  The shadow is written by
// the control plane; apply() is called only by the audio callback.  The raw
// destination remains a float buffer because that is what LV2 plugins require.
class Lv2ControlShadow {
public:
    explicit Lv2ControlShadow(std::size_t port_count)
        : size_(port_count), values_(std::make_unique<std::atomic<std::uint32_t>[]>(port_count)), input_mask_(port_count, 0) {}

    Lv2ControlShadow(const Lv2ControlShadow&) = delete;
    Lv2ControlShadow& operator=(const Lv2ControlShadow&) = delete;

    bool configure_input(std::size_t port, float initial) noexcept {
        if (port >= size_) return false;
        input_mask_[port] = 1;
        values_[port].store(to_bits(initial), std::memory_order_relaxed);
        return true;
    }

    bool set(std::size_t port, float value) noexcept {
        if (port >= size_ || !input_mask_[port] || !std::isfinite(value)) return false;
        values_[port].store(to_bits(value), std::memory_order_relaxed);
        return true;
    }

    // The immutable mask means every valid input is copied exactly once per
    // callback.  No allocation, locking, or external code runs here.
    void apply(float* controls) const noexcept {
        for (std::size_t port = 0; port < size_; ++port) {
            if (input_mask_[port]) {
                controls[port] = from_bits(values_[port].load(std::memory_order_relaxed));
            }
        }
    }

private:
    static std::uint32_t to_bits(float value) noexcept {
        static_assert(sizeof(float) == sizeof(std::uint32_t), "unexpected float representation");
        std::uint32_t bits{};
        std::memcpy(&bits, &value, sizeof(bits));
        return bits;
    }

    static float from_bits(std::uint32_t bits) noexcept {
        float value{};
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }

    static_assert(std::atomic<std::uint32_t>::is_always_lock_free,
                  "LV2 control shadow requires lock-free uint32 atomics");

    std::size_t size_{};
    std::unique_ptr<std::atomic<std::uint32_t>[]> values_;
    std::vector<unsigned char> input_mask_;
};

}  // namespace anharmonic
