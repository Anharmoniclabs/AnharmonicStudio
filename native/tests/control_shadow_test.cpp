#include "anharmonic/control_shadow.hpp"
#include "anharmonic/engine.hpp"

#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <iostream>
#include <limits>
#include <thread>
#include <vector>

namespace {

std::uint32_t bits(float value) {
    std::uint32_t result{};
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

bool one_of(std::uint32_t value, const std::array<float, 5>& choices) {
    for (float choice : choices) if (value == bits(choice)) return true;
    return false;
}

// A device-free stand-in for the relevant LV2 ABI: the instance keeps the
// float pointer supplied at connect time and reads it only while run executes.
class FakeLv2Instance {
public:
    void connect_port(std::size_t port, float* value) noexcept {
        if (port == 1) input_ = value;
    }

    void run() noexcept {
        observed_ = input_ ? *input_ : std::numeric_limits<float>::quiet_NaN();
        ++runs_;
    }

    const float* connected_input() const noexcept { return input_; }
    float observed() const noexcept { return observed_; }
    std::size_t runs() const noexcept { return runs_; }

private:
    float* input_{nullptr};
    float observed_{0.0f};
    std::size_t runs_{0};
};

// Mirrors the native engine's control-plane contract without opening a device.
// It uses the production lifecycle gate and the same connect/apply/run order.
class FakeLv2Graph {
public:
    FakeLv2Graph() : shadow_(controls_.size()) {
        shadow_.configure_input(1, 0.25f);
        instance_.connect_port(1, &controls_[1]);
    }

    bool load() {
        auto lock = lifecycle_.lock();
        if (lifecycle_.running_unlocked()) return false;
        loaded_ = true;
        return true;
    }

    bool clear() {
        auto lock = lifecycle_.lock();
        if (lifecycle_.running_unlocked()) return false;
        loaded_ = false;
        return true;
    }

    bool start() {
        auto lock = lifecycle_.lock();
        if (lifecycle_.running_unlocked() || !loaded_) return false;
        lifecycle_.set_running_unlocked(true);
        return true;
    }

    void stop() {
        auto lock = lifecycle_.lock();
        lifecycle_.set_running_unlocked(false);
    }

    bool set(float value) {
        auto lock = lifecycle_.lock();
        return loaded_ && shadow_.set(1, value);
    }

    void run() noexcept {
        shadow_.apply(controls_.data());
        instance_.run();
    }

    const float* connected_input() const noexcept { return instance_.connected_input(); }
    float observed() const noexcept { return instance_.observed(); }
    std::size_t runs() const noexcept { return instance_.runs(); }

private:
    anharmonic::Lv2LifecycleGate lifecycle_;
    std::array<float, 3> controls_{{9.0f, 0.0f, 7.0f}};
    anharmonic::Lv2ControlShadow shadow_;
    FakeLv2Instance instance_;
    bool loaded_{false};
};

}  // namespace

int main() {
    int failures = 0;
    auto check = [&](bool ok, const char* message) {
        if (!ok) { std::cerr << message << '\n'; ++failures; }
    };

    anharmonic::Lv2ControlShadow shadow(4);
    check(shadow.configure_input(1, 0.25f), "configure input control");
    check(!shadow.configure_input(4, 0.0f), "reject invalid configuration port");
    std::array<float, 4> controls{{9.0f, 0.0f, 8.0f, 7.0f}};
    shadow.apply(controls.data());
    check(controls[1] == 0.25f, "initial input control value");
    check(controls[0] == 9.0f && controls[2] == 8.0f && controls[3] == 7.0f,
          "only input controls are copied");
    check(!shadow.set(0, 0.5f), "reject non-input control");
    check(!shadow.set(4, 0.5f), "reject invalid control");
    check(!shadow.set(1, std::numeric_limits<float>::quiet_NaN()), "reject NaN control");
    check(!shadow.set(1, std::numeric_limits<float>::infinity()), "reject infinite control");

    const std::array<float, 5> published{{0.25f, -0.75f, 0.125f, 0.5f, 1.0f}};
    constexpr unsigned int kHandshakeIterations = 10000;
    std::atomic<bool> reader_go{false};
    std::atomic<bool> storm_done{false};
    std::atomic<unsigned int> writers_ready{0};
    std::atomic<unsigned int> produced{0};
    std::atomic<unsigned int> applied{0};
    std::vector<std::thread> writers;
    writers.emplace_back([&] {
        writers_ready.fetch_add(1, std::memory_order_release);
        while (!reader_go.load(std::memory_order_acquire)) std::this_thread::yield();
        for (unsigned int iteration = 1; iteration <= kHandshakeIterations; ++iteration) {
            if (!shadow.set(1, published[iteration % published.size()])) std::terminate();
            produced.store(iteration, std::memory_order_release);
            while (applied.load(std::memory_order_acquire) < iteration) std::this_thread::yield();
        }
    });
    for (unsigned int writer = 1; writer < published.size(); ++writer) {
        writers.emplace_back([&, writer] {
            writers_ready.fetch_add(1, std::memory_order_release);
            while (!reader_go.load(std::memory_order_acquire)) std::this_thread::yield();
            while (!storm_done.load(std::memory_order_acquire)) {
                if (!shadow.set(1, published[writer])) std::terminate();
            }
        });
    }
    while (writers_ready.load(std::memory_order_acquire) != writers.size()) std::this_thread::yield();
    reader_go.store(true, std::memory_order_release);
    for (unsigned int iteration = 1; iteration <= kHandshakeIterations; ++iteration) {
        while (produced.load(std::memory_order_acquire) < iteration) std::this_thread::yield();
        shadow.apply(controls.data());
        check(one_of(bits(controls[1]), published), "audio thread saw unpublished control bits");
        applied.store(iteration, std::memory_order_release);
    }
    storm_done.store(true, std::memory_order_release);
    for (auto& writer : writers) writer.join();
    check(applied.load(std::memory_order_relaxed) == kHandshakeIterations,
          "reader completed the required control handshakes");

    FakeLv2Graph graph;
    check(graph.load(), "load graph while stopped");
    check(graph.start(), "start graph");
    check(!graph.load() && !graph.clear(), "reject graph mutation while running");
    check(graph.set(0.5f), "set input control while running");
    graph.run();
    const float* const connected = graph.connected_input();
    check(connected != nullptr && graph.observed() == 0.5f, "fake LV2 reads applied control");

    std::atomic<bool> keep_interleaving{true};
    std::atomic<bool> interleaving_ok{true};
    std::thread mutator([&] {
        while (keep_interleaving.load(std::memory_order_acquire)) {
            if (graph.load() || graph.clear()) interleaving_ok.store(false, std::memory_order_release);
        }
    });
    std::thread setter([&] {
        for (unsigned int iteration = 0; iteration < kHandshakeIterations; ++iteration) {
            if (!graph.set(published[iteration % published.size()])) {
                interleaving_ok.store(false, std::memory_order_release);
            }
        }
    });
    for (unsigned int iteration = 0; iteration < kHandshakeIterations; ++iteration) {
        graph.run();
        check(graph.connected_input() == connected, "LV2 input control pointer stays stable during run");
        check(one_of(bits(graph.observed()), published), "fake LV2 observed invalid control bits");
    }
    setter.join();
    keep_interleaving.store(false, std::memory_order_release);
    mutator.join();
    check(interleaving_ok.load(std::memory_order_acquire), "running lifecycle blocks load and clear");
    check(graph.runs() == kHandshakeIterations + 1, "fake LV2 ran once per applied block");
    graph.stop();
    check(graph.clear() && graph.load(), "graph mutation resumes after stop");
    return failures ? 1 : 0;
}
