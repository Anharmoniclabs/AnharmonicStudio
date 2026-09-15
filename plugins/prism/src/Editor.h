// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "Processor.h"
class PrismEditor final : public juce::AudioProcessorEditor,
                          private juce::Timer {
public:
  explicit PrismEditor(PrismProcessor &);
  ~PrismEditor() override;
  void paint(juce::Graphics &) override;
  void resized() override;

private:
  PrismProcessor &processor;
  juce::LookAndFeel_V4 look;
  juce::TextEditor search;
  juce::ComboBox presets, arps, page;
  juce::TextButton previous{"<"}, next{">"}, mutate{"Mutate"},
      undoButton{"Undo"}, ab{"Store A"}, swap{"Recall A"}, save{"Save sound"},
      load{"Load sound"}, panicButton{"Panic"};
  juce::MidiKeyboardComponent keyboard;
  std::array<juce::Slider, parameterCount> sliders;
  std::array<juce::Label, parameterCount> labels;
  std::array<
      std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment>,
      parameterCount>
      attachments;
  juce::var bank, arpBank, snapshot;
  std::vector<int> filtered;
  std::unique_ptr<juce::FileChooser> chooser;
  juce::dsp::FFT fft{9};
  std::array<float, 1024> fftData{};
  std::array<float, 512> waveform{};
  juce::Rectangle<int> scopeArea;
  void timerCallback() override;
  void filter();
  void choose(bool);
};
