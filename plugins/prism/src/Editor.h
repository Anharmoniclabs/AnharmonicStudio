// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "Canvas.h"
class PrismLook final : public juce::LookAndFeel_V4 {
public:
  juce::Label *createSliderTextBox(juce::Slider &s) override {
    auto *label = juce::LookAndFeel_V4::createSliderTextBox(s);
    label->setColour(juce::Label::outlineColourId,
                     juce::Colours::transparentBlack);
    label->setColour(juce::Label::backgroundColourId,
                     juce::Colours::transparentBlack);
    return label;
  }
  void drawRotarySlider(juce::Graphics &g, int x, int y, int width, int height,
                        float position, float start, float end,
                        juce::Slider &) override {
    using namespace juce;
    float radius =
        std::max(5.f, std::min(float(width), float(height)) * .5f - 8);
    Point<float> center(x + width * .5f, y + height * .5f);
    float angle = start + position * (end - start);
    auto circle = Rectangle<float>(radius * 2, radius * 2).withCentre(center);
    g.setColour(Colour(0xff08101b));
    g.fillEllipse(circle.expanded(3).translated(0, 3));
    ColourGradient metal(Colour(0xff3b5069), center.x, center.y - radius,
                         Colour(0xff172537), center.x, center.y + radius,
                         false);
    g.setGradientFill(metal);
    g.fillEllipse(circle.reduced(7));
    g.setColour(Colour(0xff536b84));
    g.drawEllipse(circle.reduced(7), 1);
    Path track;
    track.addCentredArc(center.x, center.y, radius, radius, 0, start, end,
                        true);
    g.setColour(Colour(0xff2b3d56));
    g.strokePath(track, PathStrokeType(4));
    Path fill;
    fill.addCentredArc(center.x, center.y, radius, radius, 0, start, angle,
                       true);
    g.setColour(Colour(0xff8df0d5));
    g.strokePath(fill, PathStrokeType(4));
    Point<float> tip(center.x + std::sin(angle) * (radius - 11),
                     center.y - std::cos(angle) * (radius - 11));
    Point<float> base(center.x + std::sin(angle) * (radius * .3f),
                      center.y - std::cos(angle) * (radius * .3f));
    g.setColour(Colour(0xffe9fff9));
    g.drawLine(Line<float>(base, tip), 2.5f);
  }
};
class PrismEditor final : public juce::AudioProcessorEditor,
                          private juce::Timer,
                          private juce::ListBoxModel {
public:
  explicit PrismEditor(PrismProcessor &);
  ~PrismEditor() override;
  void paint(juce::Graphics &) override;
  void resized() override;

private:
  PrismProcessor &processor;
  PrismLook look;
  juce::TextEditor search;
  juce::ComboBox category, arps;
  juce::ListBox browser;
  juce::Label soundTitle, libraryTitle;
  std::array<juce::TextButton, 5> pages;
  juce::TextButton favorite{"Star sound"}, favoritesOnly{"Starred"},
      layerA{"Load to A"}, layerB{"Load to B"}, mutate{"Mutate"},
      undoButton{"Undo"}, ab{"Store A/B"}, swap{"Swap A/B"}, save{"Save sound"},
      load{"Load sound"}, panicButton{"Panic"};
  juce::MidiKeyboardComponent keyboard;
  PrismCanvas morph, filterPad, envelope, sequence;
  std::array<juce::Slider, parameterCount> sliders;
  std::array<juce::Label, parameterCount> labels;
  std::array<
      std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment>,
      parameterCount>
      attachments;
  juce::var bank, arpBank, snapshot;
  std::vector<int> filtered;
  juce::StringArray starred;
  int page = 0, selected = -1;
  std::unique_ptr<juce::FileChooser> chooser;
  juce::dsp::FFT fft{9};
  std::array<float, 1024> fftData{};
  std::array<float, 512> waveform{};
  juce::Rectangle<int> scopeArea, browserArea, editArea;
  void timerCallback() override;
  void filter();
  void choose(bool);
  int getNumRows() override { return int(filtered.size()); }
  void paintListBoxItem(int, juce::Graphics &, int, int, bool) override;
  void selectedRowsChanged(int) override;
  void listBoxItemDoubleClicked(int, const juce::MouseEvent &) override;
  void returnKeyPressed(int row) override;
};
