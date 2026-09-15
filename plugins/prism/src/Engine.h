// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
extern "C" void mpc_synth(float *, size_t, const double *, const double *,
                          double *, int64_t, int64_t);

namespace prism {
// Fixed capacity rendering: no allocation, locking, or UI work in this engine.
struct Patch {
  double osc1 = 0, osc2 = 3, mix = .42, octave = 0, detune = 8, pulse = .5,
         sub = .18, noise = .015;
  double attack = .025, decay = .32, sustain = .68, release = .65,
         cutoff = 2400, resonance = .28;
  double filterEnv = .38, drive = .18, spread = .42, lfoRate = .32,
         lfoPitch = 2, lfoFilter = .08, volume = .42;
};
struct Voice {
  std::array<double, 19> state{};
  int note = -1, channel = 1;
  int64_t age = 0, gate = -1;
  double velocity = 0;
  uint32_t random = 1;
};
class Engine {
public:
  Patch patch;
  double sr = 48000;
  std::array<Voice, 32> voices{};
  std::array<double, 16> bend{};
  std::array<bool, 16> sustain{};
  std::array<bool, 2048> keys{};
  void reset() {
    for (auto &v : voices)
      v = Voice{};
    keys.fill(false);
    sustain.fill(false);
    bend.fill(0);
  }
  void on(int note, int channel, double velocity) {
    if (note < 0 || note > 127 || channel < 1 || channel > 16)
      return;
    keys[(channel - 1) * 128 + note] = true;
    Voice *chosen = nullptr;
    for (auto &v : voices)
      if (v.note < 0) {
        chosen = &v;
        break;
      }
    if (!chosen)
      chosen =
          &*std::max_element(voices.begin(), voices.end(),
                             [](auto &a, auto &b) { return a.age < b.age; });
    *chosen = Voice{};
    chosen->note = note;
    chosen->channel = channel;
    chosen->velocity = velocity;
    chosen->random = uint32_t(note * 7919 + channel);
  }
  void off(int note, int channel) {
    if (note < 0 || note > 127 || channel < 1 || channel > 16)
      return;
    keys[(channel - 1) * 128 + note] = false;
    if (!sustain[channel - 1])
      for (auto &v : voices)
        if (v.note == note && v.channel == channel && v.gate < 0)
          v.gate = v.age;
  }
  void pedal(int channel, bool down) {
    sustain[channel - 1] = down;
    if (!down)
      for (auto &v : voices)
        if (v.note >= 0 && v.channel == channel &&
            !keys[(channel - 1) * 128 + v.note] && v.gate < 0)
          v.gate = v.age;
  }
  void render(float *left, float *right, int count) {
    while (count > 0) {
      int n = std::min(count, 64);
      std::array<float, 128> out{};
      std::array<double, 128> noise{};
      for (auto &v : voices)
        if (v.note >= 0) {
          for (int i = 0; i < n * 2; ++i) {
            v.random ^= v.random << 13;
            v.random ^= v.random >> 17;
            v.random ^= v.random << 5;
            noise[i] = (double(v.random) / 4294967295.0 * 2 - 1);
          }
          double hz =
              440 * std::exp2((v.note - 69 + bend[v.channel - 1]) / 12.0);
          double p[23] = {
              sr,
              hz,
              hz * std::exp2(patch.octave + patch.detune / 1200.0),
              std::max(.01, patch.lfoRate),
              1.0 /
                  std::max(1.0, std::floor(std::max(.0005, patch.attack) * sr)),
              (1 - patch.sustain) /
                  std::max(1.0, std::floor(std::max(.001, patch.decay) * sr)),
              patch.sustain,
              std::max(1.0, std::floor(std::max(.005, patch.release) * sr)),
              patch.osc1,
              patch.osc2,
              patch.mix,
              patch.spread * .32,
              patch.pulse,
              patch.sub,
              patch.noise,
              patch.lfoPitch,
              patch.lfoFilter,
              patch.filterEnv,
              patch.cutoff,
              2 - 1.92 * std::min(.98, patch.resonance),
              1 + patch.drive * 9,
              v.velocity * patch.volume,
              1 / std::max(1.0, 1 + patch.sub + patch.noise)};
          mpc_synth(out.data(), size_t(n), noise.data(), p, v.state.data(),
                    v.age, v.gate);
          v.age += n;
          if (v.state[7])
            v.note = -1;
        }
      for (int i = 0; i < n; ++i) {
        left[i] = out[i * 2];
        right[i] = out[i * 2 + 1];
      }
      left += n;
      right += n;
      count -= n;
    }
  }
};
} // namespace prism
