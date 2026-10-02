// Renders the desktop Prism/Native voice engine (plugins/prism/src/Engine.h and
// mpclab/native/synth.cpp) for a fixed note script so the web port can be
// compared sample by sample. Output: little-endian float32 interleaved stereo.
#include "../../../plugins/prism/src/Engine.h"
#include <cstdio>
#include <vector>

int main(int argc, char **argv) {
  int scenario = argc > 1 ? std::atoi(argv[1]) : 0;
  prism::Engine engine;
  engine.sr = 48000;
  if (scenario == 1) {  // pulse + triangle, resonant, driven, fast LFO
    engine.patch.osc1 = 3; engine.patch.osc2 = 2; engine.patch.pulse = .27;
    engine.patch.cutoff = 900; engine.patch.resonance = .9; engine.patch.drive = .7;
    engine.patch.lfoRate = 6; engine.patch.lfoPitch = 25; engine.patch.lfoFilter = .5;
    engine.patch.attack = .002; engine.patch.decay = .05; engine.patch.sustain = .3; engine.patch.release = .03;
    engine.patch.octave = -1; engine.patch.detune = 17; engine.patch.sub = .6; engine.patch.noise = .2;
  }
  if (scenario == 2) {  // sine/saw pad with bright filter, wide spread
    engine.patch.osc1 = 1; engine.patch.osc2 = 0; engine.patch.cutoff = 16000; engine.patch.spread = 1;
    engine.patch.attack = .3; engine.patch.release = .2; engine.patch.mix = .8;
  }
  const int total = 12000;
  std::vector<float> left(total), right(total);
  const int blocks[] = {97, 64, 1, 300, 33};
  int at = 0, b = 0;
  while (at < total) {
    if (at == 0) engine.on(60, 1, .8);
    if (at >= 1500 && at < 1500 + 400) {}
    int n = std::min(blocks[b++ % 5], total - at);
    // Events land exactly on block boundaries chosen by the script below.
    if (at <= 2000 && at + n > 2000) n = 2000 - at;
    if (at <= 5000 && at + n > 5000) n = 5000 - at;
    if (at <= 7000 && at + n > 7000) n = 7000 - at;
    if (n == 0) n = 1;
    engine.render(left.data() + at, right.data() + at, n);
    at += n;
    if (at == 2000) engine.on(67, 1, .6);
    if (at == 5000) engine.off(60, 1);
    if (at == 7000) { engine.on(55, 1, 1.0); engine.off(67, 1); }
  }
  for (int i = 0; i < total; ++i) { std::fwrite(&left[i], 4, 1, stdout); std::fwrite(&right[i], 4, 1, stdout); }
  return 0;
}
