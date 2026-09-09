#include "anharmonic/engine.hpp"

#include <alsa/asoundlib.h>
#include <lilv/lilv.h>
#include <portaudio.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <thread>
#include <utility>

namespace anharmonic {
namespace {
constexpr double kTwoPi = 6.283185307179586476925286766559;
constexpr float kAttack = 0.018f;
constexpr float kRelease = 0.0025f;
constexpr float kCeiling = 0.891250938f;

std::runtime_error pa_error(PaError code, const char* where) {
    return std::runtime_error(std::string(where) + ": " + Pa_GetErrorText(code));
}

float midi_note_hz(std::uint8_t note) noexcept {
    return 440.0f * std::pow(2.0f, (static_cast<int>(note) - 69) / 12.0f);
}
}  // namespace

class ThreadHolder {
public:
    template <typename F>
    explicit ThreadHolder(F&& fn) : thread_(std::forward<F>(fn)) {}
    ~ThreadHolder() { if (thread_.joinable()) thread_.join(); }
    void join() { if (thread_.joinable()) thread_.join(); }
private:
    std::thread thread_;
};

bool MidiRing::push(const MidiEvent& event) noexcept {
    const auto head = head_.load(std::memory_order_relaxed);
    const auto next = (head + 1) % Capacity;
    if (next == tail_.load(std::memory_order_acquire)) {
        dropped_.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    data_[head] = event;
    head_.store(next, std::memory_order_release);
    return true;
}

bool MidiRing::pop(MidiEvent& event) noexcept {
    const auto tail = tail_.load(std::memory_order_relaxed);
    if (tail == head_.load(std::memory_order_acquire)) return false;
    event = data_[tail];
    tail_.store((tail + 1) % Capacity, std::memory_order_release);
    return true;
}

class Lv2Plugin {
public:
    Lv2Plugin(double sample_rate, unsigned long max_frames, const std::string& uri)
        : max_frames_(max_frames) {
        world_ = lilv_world_new();
        if (!world_) throw std::runtime_error("lilv_world_new failed");
        lilv_world_load_all(world_);

        LilvNode* uri_node = lilv_new_uri(world_, uri.c_str());
        if (!uri_node) throw std::runtime_error("invalid LV2 URI: " + uri);
        const LilvPlugins* plugins = lilv_world_get_all_plugins(world_);
        plugin_ = lilv_plugins_get_by_uri(plugins, uri_node);
        lilv_node_free(uri_node);
        if (!plugin_) throw std::runtime_error("LV2 plugin not found: " + uri);

        instance_ = lilv_plugin_instantiate(plugin_, sample_rate, nullptr);
        if (!instance_) throw std::runtime_error("LV2 instantiate failed: " + uri);

        const std::uint32_t count = lilv_plugin_get_num_ports(plugin_);
        controls_.resize(count, 0.0f);
        port_scratch_.resize(count);

        LilvNode* audio = lilv_new_uri(world_, LILV_URI_AUDIO_PORT);
        LilvNode* control = lilv_new_uri(world_, LILV_URI_CONTROL_PORT);
        LilvNode* input = lilv_new_uri(world_, LILV_URI_INPUT_PORT);
        LilvNode* output = lilv_new_uri(world_, LILV_URI_OUTPUT_PORT);

        for (std::uint32_t i = 0; i < count; ++i) {
            const LilvPort* port = lilv_plugin_get_port_by_index(plugin_, i);
            const bool is_audio = lilv_port_is_a(plugin_, port, audio);
            const bool is_control = lilv_port_is_a(plugin_, port, control);
            const bool is_input = lilv_port_is_a(plugin_, port, input);
            const bool is_output = lilv_port_is_a(plugin_, port, output);
            if (is_audio && is_input) audio_inputs_.push_back(i);
            if (is_audio && is_output) audio_outputs_.push_back(i);
            if (is_control) {
                LilvNode* def = nullptr;
                LilvNode* min = nullptr;
                LilvNode* max = nullptr;
                lilv_port_get_range(plugin_, port, &def, &min, &max);
                if (def && (lilv_node_is_float(def) || lilv_node_is_int(def))) {
                    controls_[i] = lilv_node_as_float(def);
                }
                lilv_node_free(def);
                lilv_node_free(min);
                lilv_node_free(max);
                lilv_instance_connect_port(instance_, i, &controls_[i]);
            }
        }
        lilv_node_free(audio);
        lilv_node_free(control);
        lilv_node_free(input);
        lilv_node_free(output);

        if (audio_inputs_.empty() || audio_outputs_.empty()) {
            throw std::runtime_error("LV2 plugin has no audio input/output ports: " + uri);
        }
        for (auto& scratch : port_scratch_) scratch.resize(max_frames_, 0.0f);
        lilv_instance_activate(instance_);
    }

    ~Lv2Plugin() {
        if (instance_) {
            lilv_instance_deactivate(instance_);
            lilv_instance_free(instance_);
        }
        if (world_) lilv_world_free(world_);
    }

    void set_control(std::uint32_t port, float value) {
        if (port >= controls_.size()) throw std::out_of_range("LV2 control port index");
        controls_[port] = value;
    }

    void process(float* interleaved, unsigned long frames) noexcept {
        if (!instance_ || frames > max_frames_) return;
        const auto n_in = std::min<std::size_t>(2, audio_inputs_.size());
        const auto n_out = std::min<std::size_t>(2, audio_outputs_.size());

        for (std::size_t c = 0; c < n_in; ++c) {
            auto& scratch = port_scratch_[audio_inputs_[c]];
            for (unsigned long f = 0; f < frames; ++f) scratch[f] = interleaved[f * 2 + c];
            lilv_instance_connect_port(instance_, audio_inputs_[c], scratch.data());
        }
        if (n_in == 1) {
            auto& scratch = port_scratch_[audio_inputs_[0]];
            for (unsigned long f = 0; f < frames; ++f) scratch[f] = 0.5f * (interleaved[f * 2] + interleaved[f * 2 + 1]);
        }
        for (std::size_t c = 0; c < n_out; ++c) {
            auto& scratch = port_scratch_[audio_outputs_[c]];
            std::fill_n(scratch.data(), frames, 0.0f);
            lilv_instance_connect_port(instance_, audio_outputs_[c], scratch.data());
        }

        lilv_instance_run(instance_, static_cast<std::uint32_t>(frames));

        if (n_out == 1) {
            const auto& out = port_scratch_[audio_outputs_[0]];
            for (unsigned long f = 0; f < frames; ++f) interleaved[f * 2] = interleaved[f * 2 + 1] = out[f];
        } else {
            for (unsigned long f = 0; f < frames; ++f) {
                interleaved[f * 2] = port_scratch_[audio_outputs_[0]][f];
                interleaved[f * 2 + 1] = port_scratch_[audio_outputs_[1]][f];
            }
        }
    }

private:
    unsigned long max_frames_{};
    LilvWorld* world_{nullptr};
    const LilvPlugin* plugin_{nullptr};
    LilvInstance* instance_{nullptr};
    std::vector<std::uint32_t> audio_inputs_;
    std::vector<std::uint32_t> audio_outputs_;
    std::vector<float> controls_;
    std::vector<std::vector<float>> port_scratch_;
};

Engine::Engine(double sample_rate, unsigned long block_size)
    : sample_rate_(sample_rate), block_size_(block_size) {
    if (sample_rate_ < 8000.0 || sample_rate_ > 384000.0) throw std::invalid_argument("invalid sample rate");
    if (block_size_ < 16 || block_size_ > 8192) throw std::invalid_argument("invalid block size");
    const PaError err = Pa_Initialize();
    if (err != paNoError) throw pa_error(err, "Pa_Initialize");
    open_midi();
}

Engine::~Engine() {
    stop();
    clear_plugins();
    close_midi();
    Pa_Terminate();
}

void Engine::start(int output_device, int input_device) {
    if (running()) return;
    PaStreamParameters out{};
    out.device = output_device >= 0 ? output_device : Pa_GetDefaultOutputDevice();
    if (out.device == paNoDevice) throw std::runtime_error("no PortAudio output device");
    const PaDeviceInfo* out_info = Pa_GetDeviceInfo(out.device);
    if (!out_info) throw std::runtime_error("invalid PortAudio output device");
    out.channelCount = 2;
    out.sampleFormat = paFloat32;
    out.suggestedLatency = out_info->defaultLowOutputLatency;

    PaStreamParameters in{};
    PaStreamParameters* in_ptr = nullptr;
    const int requested_input = input_device >= 0 ? input_device : Pa_GetDefaultInputDevice();
    if (requested_input != paNoDevice) {
        const PaDeviceInfo* in_info = Pa_GetDeviceInfo(requested_input);
        if (in_info && in_info->maxInputChannels >= 2) {
            in.device = requested_input;
            in.channelCount = 2;
            in.sampleFormat = paFloat32;
            in.suggestedLatency = in_info->defaultLowInputLatency;
            in_ptr = &in;
        }
    }

    PaError err = Pa_OpenStream(
        &stream_, in_ptr, &out, sample_rate_, block_size_, paNoFlag,
        reinterpret_cast<PaStreamCallback*>(&Engine::pa_callback), this);
    if (err != paNoError) throw pa_error(err, "Pa_OpenStream");
    err = Pa_StartStream(stream_);
    if (err != paNoError) {
        Pa_CloseStream(stream_);
        stream_ = nullptr;
        throw pa_error(err, "Pa_StartStream");
    }
    running_.store(true, std::memory_order_release);
    midi_running_.store(true, std::memory_order_release);
    midi_thread_ = std::make_unique<ThreadHolder>([this] { run_midi_thread(); });
}

void Engine::stop() noexcept {
    midi_running_.store(false, std::memory_order_release);
    if (midi_thread_) {
        midi_thread_->join();
        midi_thread_.reset();
    }
    if (stream_) {
        Pa_StopStream(stream_);
        Pa_CloseStream(stream_);
        stream_ = nullptr;
    }
    running_.store(false, std::memory_order_release);
}

int Engine::pa_callback(const void* input, void* output, unsigned long frames, const void*, unsigned long flags, void* user) noexcept {
    return static_cast<Engine*>(user)->render(static_cast<const float*>(input), static_cast<float*>(output), frames, flags);
}

int Engine::render(const float* input, float* output, unsigned long frames, unsigned long flags) noexcept {
    if (flags != 0) xruns_.fetch_add(1, std::memory_order_relaxed);
    if (!output) return paContinue;
    if (input) std::memcpy(output, input, sizeof(float) * frames * 2);
    else std::fill_n(output, frames * 2, 0.0f);

    consume_midi();
    for (unsigned long f = 0; f < frames; ++f) {
        float synth = 0.0f;
        for (auto& voice : voices_) {
            if (!voice.active) continue;
            if (voice.releasing) voice.envelope = std::max(0.0f, voice.envelope - kRelease);
            else voice.envelope = std::min(1.0f, voice.envelope + kAttack);
            if (voice.envelope <= 0.0f) { voice.active = false; continue; }
            const float saw = static_cast<float>((voice.phase / kTwoPi) * 2.0 - 1.0);
            synth += saw * voice.velocity * voice.envelope * 0.09f;
            voice.phase += voice.phase_inc;
            if (voice.phase >= kTwoPi) voice.phase -= kTwoPi;
        }
        output[f * 2] += synth;
        output[f * 2 + 1] += synth;
    }

    for (auto& plugin : plugins_) plugin->process(output, frames);

    for (unsigned long f = 0; f < frames; ++f) {
        float left = std::isfinite(output[f * 2]) ? output[f * 2] * master_gain_ : 0.0f;
        float right = std::isfinite(output[f * 2 + 1]) ? output[f * 2 + 1] * master_gain_ : 0.0f;
        const float peak = std::max(std::abs(left), std::abs(right));
        const float target = peak > kCeiling ? kCeiling / peak : 1.0f;
        limiter_gain_ = target < limiter_gain_ ? target : std::min(1.0f, limiter_gain_ + 0.0004f);
        output[f * 2] = left * limiter_gain_;
        output[f * 2 + 1] = right * limiter_gain_;
    }
    return paContinue;
}

void Engine::consume_midi() noexcept {
    MidiEvent event;
    while (midi_.pop(event)) {
        switch (event.type) {
            case MidiEvent::Type::NoteOn: note_on(event.channel, event.data1, event.data2); break;
            case MidiEvent::Type::NoteOff: note_off(event.channel, event.data1); break;
            case MidiEvent::Type::CC:
                if (event.data1 == 7) master_gain_ = std::clamp(event.data2 / 127.0f, 0.0f, 1.0f);
                if (event.data1 == 123) for (auto& v : voices_) v.releasing = true;
                break;
            case MidiEvent::Type::PitchBend: break;
        }
    }
}

void Engine::note_on(std::uint8_t channel, std::uint8_t note, std::uint8_t velocity) noexcept {
    Voice* target = nullptr;
    for (auto& voice : voices_) if (!voice.active) { target = &voice; break; }
    if (!target) target = &voices_[voice_cursor_++ % voices_.size()];
    target->active = true;
    target->releasing = false;
    target->channel = channel;
    target->note = note;
    target->velocity = velocity / 127.0f;
    target->phase = 0.0;
    target->phase_inc = kTwoPi * midi_note_hz(note) / sample_rate_;
    target->envelope = 0.0f;
}

void Engine::note_off(std::uint8_t channel, std::uint8_t note) noexcept {
    for (auto& voice : voices_) if (voice.active && voice.channel == channel && voice.note == note) voice.releasing = true;
}

void Engine::open_midi() {
    if (snd_seq_open(&seq_, "default", SND_SEQ_OPEN_DUPLEX, SND_SEQ_NONBLOCK) < 0) {
        seq_ = nullptr;
        throw std::runtime_error("snd_seq_open failed");
    }
    snd_seq_set_client_name(seq_, "Anharmonic Studio");
    seq_port_ = snd_seq_create_simple_port(
        seq_, "Anharmonic Studio MIDI In",
        SND_SEQ_PORT_CAP_WRITE | SND_SEQ_PORT_CAP_SUBS_WRITE,
        SND_SEQ_PORT_TYPE_APPLICATION | SND_SEQ_PORT_TYPE_SOFTWARE);
    if (seq_port_ < 0) throw std::runtime_error("snd_seq_create_simple_port failed");
}

void Engine::close_midi() noexcept {
    if (seq_) { snd_seq_close(seq_); seq_ = nullptr; }
    seq_port_ = -1;
}

int Engine::midi_client_id() const noexcept { return seq_ ? snd_seq_client_id(seq_) : -1; }
int Engine::midi_port_id() const noexcept { return seq_port_; }

void Engine::connect_midi_source(int client, int port) {
    if (!seq_ || seq_port_ < 0) throw std::runtime_error("MIDI sequencer is not open");
    const int rc = snd_seq_connect_from(seq_, seq_port_, client, port);
    if (rc < 0 && rc != -EBUSY) throw std::runtime_error(std::string("snd_seq_connect_from: ") + snd_strerror(rc));
}

void Engine::run_midi_thread() {
    while (midi_running_.load(std::memory_order_acquire)) {
        snd_seq_event_t* ev = nullptr;
        while (seq_ && snd_seq_event_input(seq_, &ev) >= 0 && ev) {
            MidiEvent out{};
            bool accepted = true;
            switch (ev->type) {
                case SND_SEQ_EVENT_NOTEON:
                    out.type = ev->data.note.velocity ? MidiEvent::Type::NoteOn : MidiEvent::Type::NoteOff;
                    out.channel = ev->data.note.channel; out.data1 = ev->data.note.note; out.data2 = ev->data.note.velocity; break;
                case SND_SEQ_EVENT_NOTEOFF:
                    out.type = MidiEvent::Type::NoteOff; out.channel = ev->data.note.channel; out.data1 = ev->data.note.note; out.data2 = ev->data.note.velocity; break;
                case SND_SEQ_EVENT_CONTROLLER:
                    out.type = MidiEvent::Type::CC; out.channel = ev->data.control.channel; out.data1 = static_cast<std::uint8_t>(ev->data.control.param); out.data2 = static_cast<std::uint8_t>(std::clamp(ev->data.control.value, 0, 127)); break;
                case SND_SEQ_EVENT_PITCHBEND:
                    out.type = MidiEvent::Type::PitchBend; out.channel = ev->data.control.channel; out.bend = static_cast<std::int16_t>(std::clamp(ev->data.control.value, -8192, 8191)); break;
                default: accepted = false; break;
            }
            if (accepted) midi_.push(out);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

std::vector<std::string> Engine::list_lv2_plugins() const {
    LilvWorld* world = lilv_world_new();
    if (!world) throw std::runtime_error("lilv_world_new failed");
    lilv_world_load_all(world);
    const LilvPlugins* plugins = lilv_world_get_all_plugins(world);
    std::vector<std::string> result;
    LILV_FOREACH(plugins, i, plugins) {
        const LilvPlugin* plugin = lilv_plugins_get(plugins, i);
        const LilvNode* uri = lilv_plugin_get_uri(plugin);
        if (uri) result.emplace_back(lilv_node_as_uri(uri));
    }
    lilv_world_free(world);
    std::sort(result.begin(), result.end());
    return result;
}

std::size_t Engine::load_lv2(const std::string& uri) {
    if (running()) throw std::runtime_error("stop audio before modifying the LV2 graph");
    std::lock_guard<std::mutex> lock(plugin_mutex_);
    plugins_.push_back(std::make_unique<Lv2Plugin>(sample_rate_, block_size_, uri));
    return plugins_.size() - 1;
}

void Engine::clear_plugins() {
    if (running()) throw std::runtime_error("stop audio before modifying the LV2 graph");
    std::lock_guard<std::mutex> lock(plugin_mutex_);
    plugins_.clear();
}

void Engine::set_plugin_control(std::size_t plugin_index, std::uint32_t port_index, float value) {
    if (plugin_index >= plugins_.size()) throw std::out_of_range("plugin index");
    plugins_[plugin_index]->set_control(port_index, value);
}

}  // namespace anharmonic
