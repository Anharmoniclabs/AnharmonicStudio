// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "Bank.h"
#include "Engine.h"
#include <juce_audio_utils/juce_audio_utils.h>
#include <juce_dsp/juce_dsp.h>
class PrismProcessor final : public juce::AudioProcessor {
public:
  PrismProcessor();
  const juce::String getName() const override { return "Anharmonic Prism"; }
  void prepareToPlay(double, int) override;
  void releaseResources() override;
  void processBlock(juce::AudioBuffer<float> &, juce::MidiBuffer &) override;
  bool isBusesLayoutSupported(const BusesLayout &l) const override {
    return l.getMainOutputChannelSet() == juce::AudioChannelSet::stereo() &&
           l.getMainInputChannelSet().isDisabled();
  }
  bool acceptsMidi() const override { return true; }
  bool producesMidi() const override { return false; }
  double getTailLengthSeconds() const override { return 20; }
  bool hasEditor() const override { return true; }
  juce::AudioProcessorEditor *createEditor() override;
  int getNumPrograms() override { return 1; }
  int getCurrentProgram() override { return 0; }
  void setCurrentProgram(int) override {}
  const juce::String getProgramName(int) override { return "Current sound"; }
  void changeProgramName(int, const juce::String &) override {}
  void getStateInformation(juce::MemoryBlock &) override;
  void setStateInformation(const void *, int) override;
  void setValue(int, float);
  void loadSound(const juce::var &);
  void loadArp(const juce::var &);
  juce::var exportSound() const;
  juce::UndoManager undo;
  juce::AudioProcessorValueTreeState state;
  juce::MidiKeyboardState keyboard;
  std::array<std::atomic<float>, 512> scope{};
  std::atomic<int> scopeWrite{0};
  std::atomic<bool> panic{false};

private:
  static juce::AudioProcessorValueTreeState::ParameterLayout layout();
  std::array<std::atomic<float> *, parameterCount> values{};
  prism::Engine engine;
  juce::Reverb reverb;
  juce::dsp::Chorus<float> chorus;
  juce::AudioBuffer<float> delay;
  int delayIndex = 0;
  double tempo = 120, arpNext = 0, arpGate = 0;
  int arpIndex = 0, arpNote = -1, arpChannel = 1;
  bool arpEnabled = false, wasPlaying = false;
  std::array<float, 2048> held{};
  uint32_t rng = 0x12345678;
  void midi(const juce::MidiMessage &);
  void render(float *, float *, int);
  void readPatch();
  void resetNotes();
};
