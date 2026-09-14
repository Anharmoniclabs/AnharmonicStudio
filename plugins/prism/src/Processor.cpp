// SPDX-License-Identifier: GPL-3.0-or-later
#include "Processor.h"
#include "Editor.h"
using namespace juce;
namespace {
constexpr double divisions[] = {.125, 1.0 / 3, .25, .5, .75, 1};
}
AudioProcessorValueTreeState::ParameterLayout PrismProcessor::layout() {
  AudioProcessorValueTreeState::ParameterLayout result;
  for (auto &s : specs) {
    NormalisableRange<float> r(s.lo, s.hi, s.step);
    if (String(s.id) == "cutoff" || String(s.id) == "attack" ||
        String(s.id) == "decay" || String(s.id) == "release" ||
        String(s.id) == "lfo_rate")
      r.skew = .3f;
    auto format = [id = String(s.id)](float v, int) {
      if (id == "osc1" || id == "osc2")
        return StringArray{"Saw", "Sine", "Triangle",
                           "Pulse"}[std::clamp(int(v), 0, 3)];
      if (id == "arp_mode")
        return StringArray{"Up", "Down", "Up/down",
                           "Random"}[std::clamp(int(v), 0, 3)];
      if (id == "arp_on")
        return String(v > .5f ? "On" : "Off");
      if (id == "arp_rate" || id == "echo_rate")
        return StringArray{
            "1/32", "1/8 T", "1/16", "1/8", "1/8 D", "1/4"}[std::clamp(int(v),
                                                                       0, 5)];
      if (id == "osc2_octave" || id == "arp_octaves")
        return String(int(v));
      if (id == "cutoff")
        return String(v, 0) + " Hz";
      if (id == "lfo_rate")
        return String(v, 2) + " Hz";
      if (id == "attack" || id == "decay" || id == "release")
        return String(v, 3) + " s";
      if (id == "detune" || id == "lfo_pitch")
        return String(v, 1) + " ct";
      return String(v, 2);
    };
    result.add(std::make_unique<AudioParameterFloat>(
        ParameterID(s.id, 1), s.label, r, s.initial,
        AudioParameterFloatAttributes().withStringFromValueFunction(format)));
  }
  return result;
}
PrismProcessor::PrismProcessor()
    : AudioProcessor(BusesProperties().withOutput(
          "Stereo", AudioChannelSet::stereo(), true)),
      state(*this, &undo, "Prism", layout()) {
  for (int i = 0; i < parameterCount; ++i)
    values[i] = state.getRawParameterValue(specs[i].id);
  for (auto &s : scope)
    s.store(0);
}
void PrismProcessor::resetNotes() {
  engine.reset();
  held.fill(0);
  arpNote = -1;
  arpIndex = 0;
  arpNext = arpGate = 0;
}
void PrismProcessor::prepareToPlay(double sr, int frames) {
  engine.sr = sr;
  resetNotes();
  delay.setSize(2, int(sr * 4) + 1);
  delay.clear();
  delayIndex = 0;
  reverb.setSampleRate(sr);
  reverb.reset();
  chorus.prepare({sr, juce::uint32(std::max(1, frames)), 2});
  chorus.reset();
  setLatencySamples(1);
}
void PrismProcessor::releaseResources() {
  resetNotes();
  reverb.reset();
  chorus.reset();
  delay.clear();
}
void PrismProcessor::readPatch() {
  auto val = [&](int i) { return double(values[i]->load()); };
  engine.patch = {val(0),  val(1),  val(2),  val(3),  val(4),  val(5),
                  val(6),  val(7),  val(8),  val(9),  val(10), val(11),
                  val(12), val(13), val(14), val(15), val(16), val(17),
                  val(18), val(19), val(20)};
  bool enabled = val(21) > .5;
  if (enabled != arpEnabled) {
    resetNotes();
    arpEnabled = enabled;
  }
}
void PrismProcessor::midi(const MidiMessage &m) {
  int c = m.getChannel();
  if (m.isAllNotesOff() || m.isAllSoundOff()) {
    resetNotes();
    return;
  }
  if (c < 1 || c > 16)
    return;
  if (m.isPitchWheel()) {
    engine.bend[c - 1] = (m.getPitchWheelValue() - 8192) / 8192.0 * 2;
    return;
  }
  if (m.isController() && m.getControllerNumber() == 64) {
    engine.pedal(c, m.getControllerValue() >= 64);
    if (m.getControllerValue() < 64)
      for (int n = 0; n < 128; ++n)
        if (held[(c - 1) * 128 + n] < 0)
          held[(c - 1) * 128 + n] = 0;
    return;
  }
  if (m.isNoteOn()) {
    int n = m.getNoteNumber();
    held[(c - 1) * 128 + n] = m.getFloatVelocity();
    if (!arpEnabled)
      engine.on(n, c, m.getFloatVelocity());
  } else if (m.isNoteOff()) {
    int n = m.getNoteNumber();
    auto &h = held[(c - 1) * 128 + n];
    h = engine.sustain[c - 1] ? -std::abs(h) : 0;
    if (!arpEnabled)
      engine.off(n, c);
  }
}
void PrismProcessor::render(float *l, float *r, int count) {
  while (count > 0) {
    if (arpEnabled) {
      if (arpNote >= 0 && arpGate <= 0) {
        for (auto &voice : engine.voices)
          if (voice.note == arpNote && voice.channel == arpChannel &&
              voice.gate < 0)
            voice.gate = voice.age;
        arpNote = -1;
      }
      if (arpNext <= 0) {
        std::array<int, 512> notes{};
        std::array<int, 512> channels{};
        std::array<float, 512> velocities{};
        int size = 0;
        int octaves = int(values[24]->load());
        for (int o = 0; o < octaves; ++o)
          for (int n = 0; n < 128; ++n)
            for (int c = 1; c <= 16; ++c)
              if (held[(c - 1) * 128 + n] != 0 && n + o * 12 < 128 &&
                  size < 512) {
                notes[size] = n + o * 12;
                channels[size] = c;
                velocities[size] = std::abs(held[(c - 1) * 128 + n]);
                ++size;
              }
        if (size > 0) {
          int index = arpIndex % size, mode = int(values[23]->load());
          if (mode == 1)
            index = size - 1 - index;
          if (mode == 2 && size > 1) {
            index = arpIndex % (2 * size - 2);
            if (index >= size)
              index = 2 * size - 2 - index;
          }
          if (mode == 3) {
            rng ^= rng << 13;
            rng ^= rng >> 17;
            rng ^= rng << 5;
            index = int(rng % uint32_t(size));
          }
          arpNote = notes[index];
          arpChannel = channels[index];
          engine.on(arpNote, arpChannel, velocities[index]);
          arpIndex = (arpIndex + 1) % 1000000;
        }
        arpNext += engine.sr * 60 / tempo *
                   divisions[std::clamp(int(values[22]->load()), 0, 5)];
        arpGate = arpNext * values[25]->load();
      }
    }
    int n = std::min(count, 64);
    if (arpEnabled) {
      n = std::min(n, std::max(1, int(std::ceil(arpNext))));
      if (arpNote >= 0)
        n = std::min(n, std::max(1, int(std::ceil(arpGate))));
    }
    engine.render(l, r, n);
    if (arpEnabled) {
      arpNext -= n;
      arpGate -= n;
    }
    l += n;
    r += n;
    count -= n;
  }
}
void PrismProcessor::processBlock(AudioBuffer<float> &audio,
                                  MidiBuffer &messages) {
  ScopedNoDenormals noDenormals;
  audio.clear();
  if (audio.getNumChannels() < 2 || delay.getNumSamples() == 0)
    return;
  if (panic.exchange(false)) {
    resetNotes();
    delay.clear();
    reverb.reset();
    chorus.reset();
  }
  if (auto *ph = getPlayHead())
    if (auto pos = ph->getPosition()) {
      if (auto bpm = pos->getBpm())
        if (std::isfinite(*bpm) && *bpm > 0)
          tempo = std::clamp(*bpm, 20., 400.);
      if (wasPlaying && !pos->getIsPlaying())
        resetNotes();
      wasPlaying = pos->getIsPlaying();
    }
  readPatch();
  keyboard.processNextMidiBuffer(messages, 0, audio.getNumSamples(), true);
  float *l = audio.getWritePointer(0);
  float *r = audio.getWritePointer(1);
  int at = 0;
  for (const auto metadata : messages) {
    int end = std::clamp(metadata.samplePosition, at, audio.getNumSamples());
    if (end > at)
      render(l + at, r + at, end - at);
    at = end;
    midi(metadata.getMessage());
  }
  if (at < audio.getNumSamples())
    render(l + at, r + at, audio.getNumSamples() - at);
  chorus.setRate(.35f);
  chorus.setDepth(.3f);
  chorus.setCentreDelay(12);
  chorus.setFeedback(.12f);
  chorus.setMix(values[31]->load());
  dsp::AudioBlock<float> block(audio);
  dsp::ProcessContextReplacing<float> context(block);
  chorus.process(context);
  int length = delay.getNumSamples();
  int time =
      std::clamp(int(engine.sr * 60 / tempo *
                     divisions[std::clamp(int(values[28]->load()), 0, 5)]),
                 1, length - 1);
  float mix = values[26]->load(), feedback = values[27]->load();
  auto *dl = delay.getWritePointer(0);
  auto *dr = delay.getWritePointer(1);
  for (int i = 0; i < audio.getNumSamples(); ++i) {
    int read = (delayIndex + length - time) % length;
    float a = dl[read], b = dr[read];
    dl[delayIndex] = std::tanh(l[i] + b * feedback);
    dr[delayIndex] = std::tanh(r[i] + a * feedback);
    l[i] += a * mix;
    r[i] += b * mix;
    delayIndex = (delayIndex + 1) % length;
  }
  Reverb::Parameters rp;
  rp.roomSize = values[30]->load();
  rp.wetLevel = values[29]->load();
  rp.dryLevel = 1;
  rp.width = 1;
  rp.damping = .45f;
  reverb.setParameters(rp);
  reverb.processStereo(l, r, audio.getNumSamples());
  for (int i = 0; i < audio.getNumSamples(); ++i) {
    l[i] = std::clamp(l[i], -1.f, 1.f);
    r[i] = std::clamp(r[i], -1.f, 1.f);
    {
      int w = scopeWrite.load(std::memory_order_relaxed);
      scope[size_t(w)].store((l[i] + r[i]) * .5f, std::memory_order_relaxed);
      scopeWrite.store((w + 1) % 512, std::memory_order_release);
    }
  }
  messages.clear();
}
void PrismProcessor::setValue(int i, float value) {
  if (i < 0 || i >= parameterCount || !std::isfinite(value))
    return;
  auto *p = state.getParameter(specs[i].id);
  p->beginChangeGesture();
  p->setValueNotifyingHost(
      p->convertTo0to1(std::clamp(value, specs[i].lo, specs[i].hi)));
  p->endChangeGesture();
}
void PrismProcessor::loadArp(const var &a) {
  if (!a.isObject())
    return;
  setValue(21, bool(a.getProperty("enabled", false)) ? 1 : 0);
  double beats = a.getProperty("rate_beats", .25);
  int rate = 2;
  for (int i = 0; i < 6; ++i)
    if (std::abs(beats - divisions[i]) < .001)
      rate = i;
  setValue(22, float(rate));
  StringArray modes{"up", "down", "up/down", "random"};
  setValue(23, float(std::max(
                   0, modes.indexOf(a.getProperty("mode", "up").toString()))));
  setValue(24, float(a.getProperty("octaves", 1)));
  setValue(25, float(a.getProperty("gate", .72)));
}
void PrismProcessor::loadSound(const var &item) {
  auto p = item.getProperty("patch", {});
  if (!p.isObject())
    return;
  undo.beginNewTransaction("Load sound");
  StringArray waves{"saw", "sine", "triangle", "square"};
  for (int i = 0; i < 21; ++i) {
    auto v = p.getProperty(specs[i].id, specs[i].initial);
    setValue(i, i < 2 ? float(std::max(0, waves.indexOf(v.toString())))
                      : float(v));
  }
  if (item.hasProperty("arp"))
    loadArp(item["arp"]);
  if (auto *fx = item.getProperty("effects", {}).getDynamicObject())
    for (int i = 26; i < parameterCount; ++i)
      if (fx->hasProperty(specs[i].id))
        setValue(i, float(fx->getProperty(specs[i].id)));
  panic.store(true);
}
var PrismProcessor::exportSound() const {
  auto *obj = new DynamicObject;
  obj->setProperty("format", "anharmonic-prism");
  obj->setProperty("version", 1);
  obj->setProperty("name", "User sound");
  auto *patch = new DynamicObject;
  StringArray waves{"saw", "sine", "triangle", "square"};
  for (int i = 0; i < 21; ++i) {
    float v = values[i]->load();
    patch->setProperty(specs[i].id,
                       i < 2 ? var(waves[std::clamp(int(v), 0, 3)]) : var(v));
  }
  obj->setProperty("patch", var(patch));
  auto *arp = new DynamicObject;
  arp->setProperty("enabled", values[21]->load() > .5f);
  arp->setProperty("rate_beats",
                   divisions[std::clamp(int(values[22]->load()), 0, 5)]);
  StringArray modes{"up", "down", "up/down", "random"};
  arp->setProperty("mode", modes[std::clamp(int(values[23]->load()), 0, 3)]);
  arp->setProperty("octaves", int(values[24]->load()));
  arp->setProperty("gate", values[25]->load());
  obj->setProperty("arp", var(arp));
  auto *fx = new DynamicObject;
  for (int i = 26; i < parameterCount; ++i)
    fx->setProperty(specs[i].id, values[i]->load());
  obj->setProperty("effects", var(fx));
  return var(obj);
}
void PrismProcessor::getStateInformation(MemoryBlock &out) {
  auto tree = state.copyState();
  if (auto xml = tree.createXml())
    copyXmlToBinary(*xml, out);
}
void PrismProcessor::setStateInformation(const void *data, int size) {
  if (size < 0 || size > 1024 * 1024)
    return;
  if (auto xml = getXmlFromBinary(data, size))
    if (xml->hasTagName("Prism")) {
      auto tree = ValueTree::fromXml(*xml);
      for (auto child : tree) {
        String id = child["id"];
        auto *p = state.getParameter(id);
        double v = child["value"];
        if (!p || !std::isfinite(v) || v < p->getNormalisableRange().start ||
            v > p->getNormalisableRange().end)
          return;
      }
      state.replaceState(tree);
      panic.store(true);
    }
}
AudioProcessorEditor *PrismProcessor::createEditor() {
  return new PrismEditor(*this);
}
AudioProcessor *JUCE_CALLTYPE createPluginFilter() {
  return new PrismProcessor;
}
