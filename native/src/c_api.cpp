#include "anharmonic/engine.hpp"

#include <cstring>
#include <exception>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
thread_local std::string g_error;

template <typename F>
int guard(F&& fn) noexcept {
    try {
        fn();
        g_error.clear();
        return 0;
    } catch (const std::exception& exc) {
        g_error = exc.what();
        return -1;
    } catch (...) {
        g_error = "unknown native engine error";
        return -1;
    }
}

struct Handle {
    std::unique_ptr<anharmonic::Engine> engine;
    std::vector<std::string> plugin_cache;
};
}

extern "C" {

const char* anh_last_error() noexcept { return g_error.c_str(); }

void* anh_engine_create(double sample_rate, unsigned long block_size) noexcept {
    try {
        auto* handle = new Handle;
        handle->engine = std::make_unique<anharmonic::Engine>(sample_rate, block_size);
        g_error.clear();
        return handle;
    } catch (const std::exception& exc) {
        g_error = exc.what();
        return nullptr;
    }
}

void anh_engine_destroy(void* ptr) noexcept {
    delete static_cast<Handle*>(ptr);
}

int anh_engine_start(void* ptr, int output_device, int input_device) noexcept {
    return guard([&] { static_cast<Handle*>(ptr)->engine->start(output_device, input_device); });
}

int anh_engine_stop(void* ptr) noexcept {
    return guard([&] { static_cast<Handle*>(ptr)->engine->stop(); });
}

int anh_engine_midi_client(void* ptr) noexcept {
    if (!ptr) return -1;
    return static_cast<Handle*>(ptr)->engine->midi_client_id();
}

int anh_engine_midi_port(void* ptr) noexcept {
    if (!ptr) return -1;
    return static_cast<Handle*>(ptr)->engine->midi_port_id();
}

int anh_engine_connect_midi(void* ptr, int client, int port) noexcept {
    return guard([&] { static_cast<Handle*>(ptr)->engine->connect_midi_source(client, port); });
}

unsigned long long anh_engine_xruns(void* ptr) noexcept {
    return ptr ? static_cast<Handle*>(ptr)->engine->xruns() : 0;
}

unsigned long long anh_engine_dropped_midi(void* ptr) noexcept {
    return ptr ? static_cast<Handle*>(ptr)->engine->dropped_midi() : 0;
}

int anh_lv2_scan(void* ptr) noexcept {
    return guard([&] { static_cast<Handle*>(ptr)->plugin_cache = static_cast<Handle*>(ptr)->engine->list_lv2_plugins(); });
}

unsigned long anh_lv2_count(void* ptr) noexcept {
    return ptr ? static_cast<unsigned long>(static_cast<Handle*>(ptr)->plugin_cache.size()) : 0;
}

int anh_lv2_uri(void* ptr, unsigned long index, char* buffer, unsigned long capacity) noexcept {
    return guard([&] {
        auto* handle = static_cast<Handle*>(ptr);
        if (index >= handle->plugin_cache.size()) throw std::out_of_range("LV2 scan index");
        const auto& uri = handle->plugin_cache[index];
        if (!buffer || capacity <= uri.size()) throw std::runtime_error("LV2 URI buffer too small");
        std::memcpy(buffer, uri.data(), uri.size());
        buffer[uri.size()] = '\0';
    });
}

long anh_lv2_load(void* ptr, const char* uri) noexcept {
    try {
        if (!uri) throw std::invalid_argument("LV2 URI is null");
        const auto index = static_cast<Handle*>(ptr)->engine->load_lv2(uri);
        g_error.clear();
        return static_cast<long>(index);
    } catch (const std::exception& exc) {
        g_error = exc.what();
        return -1;
    }
}

int anh_lv2_clear(void* ptr) noexcept {
    return guard([&] { static_cast<Handle*>(ptr)->engine->clear_plugins(); });
}

int anh_lv2_set_control(void* ptr, unsigned long plugin, unsigned int port, float value) noexcept {
    return guard([&] { static_cast<Handle*>(ptr)->engine->set_plugin_control(plugin, port, value); });
}

}
