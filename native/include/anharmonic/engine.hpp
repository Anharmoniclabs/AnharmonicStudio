#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

typedef void PaStream;
struct _snd_seq;
typedef struct _snd_seq snd_seq_t;

namespace anharmonic {

struct MidiEvent {
    enum class Type : std::uint8_t { NoteOn, NoteOff, CC, PitchBend };
    Type type{Type::NoteOn};
    std::uint8_t channel{0};
    std::uint8_t data1{0};
    std::uint8_t data2{0};
    std::int16_t bend{0};
};

class MidiRing {
public:
    static constexpr std::size_t Capacity = 2048;
    bool push(const MidiEvent& event) noexcept;
    bool pop(MidiEvent& event) noexcept;
    std::uint64_t dropped() const noexcept { return dropped_.load(std::memory_order_relaxed); }

private:
    std::array<MidiEvent, Capacity> data_{};
    std::atomic<std::size_t> head_{0};
    std::atomic<std::size_t> tail_{0};
    std::atomic<std::uint64_t> dropped_{0};
};

class Lv2Plugin;

class Engine {
public:
    Engine(double sample_rate, unsigned long block_size);
    ~Engine();

    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    void start(int output_device = -1, int input_device = -1);
    void stop() noexcept;
    bool running() const noexcept { return running_.load(std::memory_order_acquire); }

    int midi_client_id() const noexcept;
    int midi_port_id() const noexcept;
    void connect_midi_source(int client, int port);

    std::vector<std::string> list_lv2_plugins() const;
    std::size_t load_lv2(const std::string& uri);
    void clear_plugins();
    void set_plugin_control(std::size_t plugin_index, std::uint32_t port_index, float value);

    std::uint64_t xruns() const noexcept { return xruns_.load(std::memory_order_relaxed); }
    std::uint64_t dropped_midi() const noexcept { return midi_.dropped(); }

private:
    struct Voice {
        bool active{false};
        std::uint8_t note{0};
        std::uint8_t channel{0};
        float velocity{0.0f};
        double phase{0.0};
        double phase_inc{0.0};
        float envelope{0.0f};
        bool releasing{false};
    };

    static int pa_callback(
        const void* input,
        void* output,
        unsigned long frame_count,
        const void* time_info,
        unsigned long status_flags,
        void* user_data) noexcept;

    int render(const float* input, float* output, unsigned long frames, unsigned long status_flags) noexcept;
    void consume_midi() noexcept;
    void note_on(std::uint8_t channel, std::uint8_t note, std::uint8_t velocity) noexcept;
    void note_off(std::uint8_t channel, std::uint8_t note) noexcept;
    void run_midi_thread();
    void open_midi();
    void close_midi() noexcept;

    double sample_rate_{48000.0};
    unsigned long block_size_{256};
    PaStream* stream_{nullptr};
    snd_seq_t* seq_{nullptr};
    int seq_port_{-1};
    std::atomic<bool> running_{false};
    std::atomic<bool> midi_running_{false};
    std::unique_ptr<class ThreadHolder> midi_thread_;
    MidiRing midi_;
    std::array<Voice, 64> voices_{};
    std::size_t voice_cursor_{0};
    std::atomic<std::uint64_t> xruns_{0};
    float master_gain_{0.85f};
    float limiter_gain_{1.0f};

    mutable std::mutex plugin_mutex_;
    std::vector<std::unique_ptr<Lv2Plugin>> plugins_;
};

}  // namespace anharmonic
