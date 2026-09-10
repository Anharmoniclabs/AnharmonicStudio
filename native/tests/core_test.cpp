// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <array>
#include <cmath>
#include <iostream>
#include <limits>
#include <thread>

int main() {
    int failures = 0;
    auto check = [&](bool ok, const char* message) {
        if (!ok) { std::cerr << message << '\n'; ++failures; }
    };
    check(anh_core_abi() == 1, "ABI version");
    check(anh_sample_voice_size() == sizeof(AnhSampleVoice), "ABI structure layout");
    std::array<float, 64> source{}, output{};
    source.fill(0.5f);
    AnhSampleVoice voice{0, 32, 1, 1, 32, 0, 0, 1, 1, 1, 1, 0, 0, 0};
    check(anh_sample_render(source.data(), 32, output.data(), 32, &voice) == 0, "sample render");
    check(output[0] == 0 && output[2] == 0.5f && voice.age == 32 && voice.dead, "voice state");
    voice.start = -1;
    check(anh_sample_render(source.data(), 32, output.data(), 32, &voice) == -1, "sample bounds");
    output.fill(2);
    output[0] = std::numeric_limits<float>::quiet_NaN();
    double limiter[2] = {1, 0};
    anh_limit(output.data(), 32, 0.89, 1.0 / 3840.0, limiter);
    for (float value : output) check(std::isfinite(value) && std::abs(value) <= 0.890001f, "limiter bounds");
    const float ir[3] = {1, 0.5f, 0.25f};
    void* convolver = anh_convolver_create(ir, 3, 2, 32);
    check(convolver != nullptr, "convolver preparation");
    output.fill(0); output[0] = output[1] = 1;
    check(anh_convolver_process(convolver, output.data(), 1) == 0, "one-frame convolution");
    output.fill(0);
    check(anh_convolver_process(convolver, output.data(), 32) == 0, "convolution tail");
    check(std::abs(output[0] - 0.5f) < 1e-7f && std::abs(output[2] - 0.25f) < 1e-7f, "tail values");
    check(anh_convolver_process(convolver, output.data(), 33) == -1, "prepared capacity");
    anh_convolver_reset(convolver);
    output.fill(0);
    anh_convolver_process(convolver, output.data(), 32);
    for (float value : output) check(value == 0, "convolver reset");
    anh_convolver_destroy(convolver);
    anh_convolver_destroy(nullptr);
    void* queue = anh_output_create(257);
    check(queue != nullptr, "output queue creation");
    constexpr int total = 17000;
    std::thread producer([&] {
        std::array<float, 34> block{};
        for (int at = 0; at < total; at += 17) {
            for (int i = 0; i < 17; ++i) block[i * 2] = block[i * 2 + 1] = static_cast<float>(at + i + 1);
            while (anh_output_write(queue, block.data(), 17) != 0) std::this_thread::yield();
        }
    });
    int received = 0;
    std::array<float, 22> incoming{};
    while (received < total) {
        anh_output_callback(nullptr, incoming.data(), 11, nullptr, 0, queue);
        for (int i = 0; i < 11; ++i) {
            if (incoming[i * 2] == 0) continue; // specified starvation silence
            ++received;
            check(incoming[i * 2] == received && incoming[i * 2 + 1] == received, "concurrent FIFO order");
        }
        std::this_thread::yield();
    }
    producer.join();
    check(anh_output_available(queue) == 0, "output queue drained");
    anh_output_destroy(queue);
    return failures ? 1 : 0;
}
