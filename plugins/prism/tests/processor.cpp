#include "Processor.h"
#include "Editor.h"
#include <cstdlib>
#include <iostream>
void check(bool value, const char *reason) {
  if (!value) {
    std::cerr << reason << '\n';
    std::exit(1);
  }
}
int main(int argc, char **argv) {
  juce::ScopedJuceInitialiser_GUI gui;
  PrismProcessor p;
  p.prepareToPlay(48000, 512);
  juce::AudioBuffer<float> audio(2, 512);
  juce::MidiBuffer midi;
  midi.addEvent(juce::MidiMessage::noteOn(1, 60, .8f), 127);
  p.processBlock(audio, midi);
  for (int i = 0; i < 127; ++i)
    check(audio.getSample(0, i) == 0, "early MIDI audio");
  check(audio.getMagnitude(0, 512) > .001, "silent processor");
  auto bank = juce::JSON::parse(factoryJSON);
  for (int i = 0; i < bank.size(); ++i) {
    p.loadSound(bank[i]);
    midi.addEvent(juce::MidiMessage::noteOn(1, 60, .7f), 0);
    p.processBlock(audio, midi);
    for (int c = 0; c < 2; ++c)
      for (int f = 0; f < 512; ++f)
        check(std::isfinite(audio.getSample(c, f)), "invalid factory audio");
  }
  p.setValue(12, 5432);
  p.setValue(26, .3f);
  p.setValue(29, .2f);
  p.setValue(31, .4f);
  juce::MemoryBlock state;
  p.getStateInformation(state);
  PrismProcessor recalled;
  recalled.setStateInformation(state.getData(), int(state.getSize()));
  check(std::abs(recalled.state.getRawParameterValue("cutoff")->load() - 5432) <
            1,
        "state cutoff mismatch");
  check(std::abs(recalled.state.getRawParameterValue("chorus")->load() - .4f) <
            .001,
        "state effect mismatch");
  auto doc = p.exportSound();
  recalled.loadSound(doc);
  check(std::abs(recalled.state.getRawParameterValue("echo")->load() - .3f) <
            .001,
        "sound effects mismatch");
  p.setValue(21, 1);
  p.setValue(11, .01f);
  p.setValue(26, 0);
  p.setValue(29, 0);
  p.setValue(31, 0);
  p.panic.store(true);
  midi.addEvent(juce::MidiMessage::controllerEvent(1, 64, 127), 0);
  midi.addEvent(juce::MidiMessage::noteOn(1, 60, .7f), 0);
  midi.addEvent(juce::MidiMessage::noteOn(1, 64, .7f), 0);
  for (int i = 0; i < 100; ++i)
    p.processBlock(audio, midi);
  midi.addEvent(juce::MidiMessage::noteOff(1, 60), 0);
  midi.addEvent(juce::MidiMessage::noteOff(1, 64), 0);
  midi.addEvent(juce::MidiMessage::controllerEvent(1, 64, 0), 1);
  for (int i = 0; i < 300; ++i)
    p.processBlock(audio, midi);
  check(audio.getMagnitude(0, 512) < 1e-6, "stuck arp after sustain release");
  // All additional controls must survive both DAW state and sound-file recall.
  for (int i = 32; i < parameterCount; ++i)
    p.setValue(i, specs[i].lo + (specs[i].hi - specs[i].lo) * .6f);
  p.getStateInformation(state);
  recalled.setStateInformation(state.getData(), int(state.getSize()));
  for (int i = 32; i < parameterCount; ++i)
    check(std::abs(p.state.getRawParameterValue(specs[i].id)->load() -
                   recalled.state.getRawParameterValue(specs[i].id)->load()) <
              .001,
          "new parameter DAW recall");
  recalled.loadSound(p.exportSound());
  for (int i = 32; i < parameterCount; ++i)
    check(std::abs(p.state.getRawParameterValue(specs[i].id)->load() -
                   recalled.state.getRawParameterValue(specs[i].id)->load()) <
              .001,
          "new parameter sound recall");
  auto performances = juce::JSON::parse(performanceJSON);
  check(performances.size() == 120, "performance bank size");
  for (int i = 0; i < performances.size(); ++i) {
    PrismProcessor performance;
    performance.prepareToPlay(48000, 512);
    performance.loadSound(performances[i]);
    midi.clear();
    if (i >= 24) {
      midi.addEvent(juce::MidiMessage::noteOn(1, 64, .8f), 0);
      midi.addEvent(juce::MidiMessage::noteOn(1, 67, .8f), 0);
    }
    midi.addEvent(juce::MidiMessage::noteOn(1, 60, .8f), 0);
    float peak = 0;
    for (int b = 0; b < 188; ++b) {
      if (b == 94) {
        for (int note : {60, 64, 67})
          midi.addEvent(juce::MidiMessage::noteOff(1, note), 0);
      }
      performance.processBlock(audio, midi);
      midi.clear();
      peak = std::max(peak, audio.getMagnitude(0, 512));
      for (int c = 0; c < 2; ++c)
        for (int f = 0; f < 512; ++f)
          check(std::isfinite(audio.getSample(c, f)) &&
                    std::abs(audio.getSample(c, f)) <= 1,
                "performance audio bounds");
    }
    check(peak > .001, "silent performance");
    if (i >= 24)
      check(peak < .98f, "expansion chord lacks headroom");
  }
  auto rendered = [](std::initializer_list<std::pair<int, float>> settings) {
    PrismProcessor synth;
    synth.prepareToPlay(48000, 512);
    for (auto setting : settings)
      synth.setValue(setting.first, setting.second);
    juce::AudioBuffer<float> block(2, 512);
    juce::MidiBuffer events;
    events.addEvent(juce::MidiMessage::noteOn(1, 60, .8f), 0);
    std::vector<float> result;
    for (int b = 0; b < 32; ++b) {
      synth.processBlock(block, events);
      result.insert(result.end(), block.getReadPointer(0),
                    block.getReadPointer(0) + 512);
    }
    return result;
  };
  auto normal = rendered({});
  auto different = [&](const std::vector<float> &other, const char *reason) {
    double difference = 0;
    for (size_t i = 0; i < other.size(); ++i)
      difference += std::abs(other[i] - normal[i]);
    check(difference > .01, reason);
  };
  different(rendered({{53, 1}, {44, 500}}),
            "layer B cutoff has no audio effect");
  different(rendered({{54, .8f}}), "tone macro has no audio effect");
  different(rendered({{55, .8f}}), "motion macro has no audio effect");
  different(rendered({{56, .8f}}), "space macro has no audio effect");
  different(rendered({{57, .8f}}), "texture macro has no audio effect");
  different(rendered({{58, .8f}, {60, 0}}),
            "step modulation has no audio effect");
  different(rendered({{69, 4}, {70, .8f}, {71, 0}}),
            "LFO routing has no audio effect");
  different(rendered({{72, 4}, {73, 1}}), "bit crusher has no audio effect");
  different(rendered({{74, 5}, {75, 1}}), "tremolo has no audio effect");
  auto silent = rendered({{53, 1}, {52, 0}});
  check(*std::max_element(silent.begin(), silent.end()) == 0,
        "layer mix leaks layer A");
  // Old sound files reset every newly introduced feature to a neutral default.
  p.loadSound(bank[0]);
  check(p.state.getRawParameterValue("layer_mix")->load() == 0,
        "legacy sound layer reset");
  check(p.state.getRawParameterValue("seq_depth")->load() == 0,
        "legacy sound motion reset");
  // The same tempo must drive Studio live/export and a DAW host playhead.
  auto atTempo = [](int bpm) {
    PrismProcessor synth;
    synth.prepareToPlay(48000, 512);
    juce::AudioBuffer<float> block(2, 512);
    juce::MidiBuffer events;
    int encoded = bpm * 100;
    for (int i = 0; i < 46; ++i) {
      events.addEvent(juce::MidiMessage::controllerEvent(16, 99, 125), 0);
      events.addEvent(
          juce::MidiMessage::controllerEvent(16, 98, 80 + (encoded >> 14)), 0);
      events.addEvent(
          juce::MidiMessage::controllerEvent(16, 6, (encoded >> 7) & 127), 0);
      events.addEvent(juce::MidiMessage::controllerEvent(16, 38, encoded & 127),
                      0);
      synth.processBlock(block, events);
    }
    return synth.motionStep.load();
  };
  check(atTempo(60) == 1 && atTempo(120) == 3,
        "Studio tempo packet not applied to motion");
  check(p.exportSound()["patch"]["osc2_octave"].isInt(),
        "sound octave must be an integer");
  if (argc > 1) {
    p.setValue(21, 0);
    midi.addEvent(juce::MidiMessage::noteOn(1, 60, .8f), 0);
    for (int i = 0; i < 15; ++i)
      p.processBlock(audio, midi);
    auto editor = std::unique_ptr<juce::AudioProcessorEditor>(p.createEditor());
    editor->setSize(1240, 940);
    auto picture = editor->createComponentSnapshot(editor->getLocalBounds());
    juce::File target =
        juce::File::getCurrentWorkingDirectory().getChildFile(argv[1]);
    auto stream = target.createOutputStream();
    check(stream != nullptr, "screenshot output");
    stream->setPosition(0);
    stream->truncate();
    juce::PNGImageFormat png;
    check(png.writeImageToStream(picture, *stream), "screenshot write");
  }
  std::cout << "Prism processor: MIDI offsets, all factory sounds, "
               "state/effect recall, sustain arp release passed\n";
}
