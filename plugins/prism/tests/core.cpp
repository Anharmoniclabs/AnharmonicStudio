#include "Engine.h"
#include <cstdlib>
#include <iostream>
#include <vector>
void check(bool v, const char *why) {
  if (!v) {
    std::cerr << why << '\n';
    std::exit(1);
  }
}
int main() {
  for (double sr : {44100., 48000., 96000., 192000.}) {
    prism::Engine a, b;
    a.sr = b.sr = sr;
    a.patch.noise = b.patch.noise = 0;
    a.on(60, 1, .8);
    b.on(60, 1, .8);
    std::vector<float> l(4096), r(4096), l2(4096), r2(4096);
    a.render(l.data(), r.data(), 4096);
    for (int i = 0; i < 4096;) {
      int n = std::min(37, 4096 - i);
      b.render(l2.data() + i, r2.data() + i, n);
      i += n;
    }
    double power = 0;
    for (int i = 0; i < 4096; ++i) {
      check(std::isfinite(l[i]), "nonfinite output");
      check(std::abs(l[i] - l2[i]) < 1e-5, "partition mismatch");
      power += l[i] * l[i];
    }
    check(power > .001, "silent note");
    a.off(60, 1);
    for (int i = 0; i < 100; ++i)
      a.render(l.data(), r.data(), 4096);
    for (auto &v : a.voices)
      check(v.note < 0, "stuck release");
    a.pedal(1, true);
    a.on(64, 1, 1);
    a.off(64, 1);
    check(a.voices[0].gate < 0, "sustain ignored");
    a.pedal(1, false);
    check(a.voices[0].gate == 0, "pedal release ignored");
    for (int i = 0; i < 100; ++i)
      a.on(i % 128, 1, 1);
    a.reset();
    a.render(l.data(), r.data(), 4096);
    for (float f : l)
      check(f == 0, "reset not silent");
  }
  std::cout << "Prism core: rates, partitioning, note release, sustain, "
               "stealing and reset passed\n";
}
