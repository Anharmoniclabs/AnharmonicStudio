// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"
#include <array>
#include <cmath>
#include <iostream>
#include <limits>

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
    return failures ? 1 : 0;
}
