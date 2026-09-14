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
  if (argc > 1) {
    p.setValue(21, 0);
    midi.addEvent(juce::MidiMessage::noteOn(1, 60, .8f), 0);
    for (int i = 0; i < 15; ++i)
      p.processBlock(audio, midi);
    auto editor = std::unique_ptr<juce::AudioProcessorEditor>(p.createEditor());
    editor->setSize(1040, 740);
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
