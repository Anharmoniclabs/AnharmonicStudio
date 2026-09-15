// SPDX-License-Identifier: GPL-3.0-or-later
#include "core.hpp"

ANH_API int anh_input_callback(const void*, void*, unsigned long, const void*, unsigned long, void*) noexcept;
struct DuplexHandles { void* input; void* output; };

ANH_API int anh_duplex_callback(const void* input, void* output, unsigned long frames,
                               const void* time, unsigned long flags, void* userdata) noexcept {
    if (!userdata || !output) return 2;
    const auto* handles = static_cast<const DuplexHandles*>(userdata);
    if (anh_input_callback(input, nullptr, frames, time, flags, handles->input)) return 2;
    return anh_output_callback(nullptr, output, frames, time, flags, handles->output);
}
