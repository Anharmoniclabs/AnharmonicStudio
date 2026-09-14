// SPDX-License-Identifier: GPL-3.0-or-later
#include "Editor.h"
using namespace juce;
PrismEditor::PrismEditor(PrismProcessor &p)
    : AudioProcessorEditor(p), processor(p),
      keyboard(p.keyboard, MidiKeyboardComponent::horizontalKeyboard) {
  look.setColour(ResizableWindow::backgroundColourId, Colour(0xff10151f));
  look.setColour(Slider::rotarySliderFillColourId, Colour(0xff79efca));
  look.setColour(Slider::rotarySliderOutlineColourId, Colour(0xff2e3c4d));
  look.setColour(Slider::textBoxOutlineColourId, Colours::transparentBlack);
  look.setColour(TextButton::buttonColourId, Colour(0xff243142));
  look.setColour(ComboBox::backgroundColourId, Colour(0xff1d2939));
  setLookAndFeel(&look);
  setResizable(true, true);
  setResizeLimits(920, 660, 1600, 1100);
  setSize(1040, 740);
  bank = JSON::parse(factoryJSON);
  arpBank = JSON::parse(arpJSON);
  search.setTextToShowWhenEmpty("Search 54 synth sounds...",
                                Colour(0xff9bacc1));
  search.onTextChange = [this] { filter(); };
  for (Component *c : std::initializer_list<Component *>{
           &search, &presets, &arps, &page, &previous, &next, &mutate,
           &undoButton, &ab, &swap, &save, &load, &panicButton, &keyboard})
    addAndMakeVisible(c);
  presets.onChange = [this] {
    int i = presets.getSelectedId() - 1;
    if (i >= 0 && i < int(filtered.size()))
      processor.loadSound(bank[filtered[size_t(i)]]);
  };
  previous.onClick = [this] {
    presets.setSelectedItemIndex(
        std::max(0, presets.getSelectedItemIndex() - 1));
  };
  next.onClick = [this] {
    presets.setSelectedItemIndex(std::min(presets.getNumItems() - 1,
                                          presets.getSelectedItemIndex() + 1));
  };
  arps.setTextWhenNothingSelected("Arpeggiator presets");
  for (int i = 0; i < arpBank.size(); ++i)
    arps.addItem(arpBank[i]["name"].toString(), i + 1);
  arps.onChange = [this] {
    processor.undo.beginNewTransaction("Arp preset");
    processor.loadArp(arpBank[arps.getSelectedId() - 1]);
  };
  page.addItem("01  Tone & envelope", 1);
  page.addItem("02  Motion & arpeggiator", 2);
  page.addItem("03  Space & effects", 3);
  page.onChange = [this] {
    resized();
    repaint();
  };
  page.setSelectedId(1, dontSendNotification);
  mutate.onClick = [this] {
    processor.undo.beginNewTransaction("Mutate tone");
    for (int i : {2, 4, 5, 12, 13, 14, 16, 19}) {
      auto *parameter = processor.state.getParameter(specs[i].id);
      float v = parameter->getValue();
      v = std::clamp(v + (Random::getSystemRandom().nextFloat() - .5f) * .24f,
                     0.f, 1.f);
      processor.setValue(i, parameter->convertFrom0to1(v));
    }
  };
  undoButton.onClick = [this] { processor.undo.undo(); };
  ab.onClick = [this] {
    snapshot = processor.exportSound();
    ab.setButtonText("A stored");
  };
  swap.onClick = [this] {
    if (snapshot.isObject()) {
      auto old = processor.exportSound();
      processor.loadSound(snapshot);
      snapshot = old;
    }
  };
  save.onClick = [this] { choose(true); };
  load.onClick = [this] { choose(false); };
  panicButton.onClick = [this] {
    processor.panic.store(true);
    processor.keyboard.allNotesOff(0);
  };
  for (int i = 0; i < parameterCount; ++i) {
    auto &s = sliders[size_t(i)];
    s.setSliderStyle(Slider::RotaryHorizontalVerticalDrag);
    s.setTextBoxStyle(Slider::TextBoxBelow, false, 90, 21);
    s.setName(specs[i].label);
    s.setTitle(specs[i].label);
    s.setDoubleClickReturnValue(true, specs[i].initial);
    if (i < 2)
      s.textFromValueFunction = [](double v) {
        return StringArray{"Saw", "Sine", "Triangle",
                           "Pulse"}[std::clamp(int(v), 0, 3)];
      };
    if (i == 21)
      s.textFromValueFunction = [](double v) {
        return v > .5 ? String("On") : String("Off");
      };
    if (i == 22 || i == 28)
      s.textFromValueFunction = [](double v) {
        return StringArray{
            "1/32", "1/8 T", "1/16", "1/8", "1/8 D", "1/4"}[std::clamp(int(v),
                                                                       0, 5)];
      };
    if (i == 23)
      s.textFromValueFunction = [](double v) {
        return StringArray{"Up", "Down", "Up / down",
                           "Random"}[std::clamp(int(v), 0, 3)];
      };
    labels[size_t(i)].setText(specs[i].label, dontSendNotification);
    labels[size_t(i)].setJustificationType(Justification::centred);
    addAndMakeVisible(s);
    addAndMakeVisible(labels[size_t(i)]);
    attachments[size_t(i)] =
        std::make_unique<AudioProcessorValueTreeState::SliderAttachment>(
            processor.state, specs[i].id, s);
    s.setTextBoxIsEditable(specs[i].step == 0);
  }
  keyboard.setAvailableRange(24, 96);
  keyboard.setLowestVisibleKey(48);
  keyboard.setKeyWidth(24);
  filter();
  startTimerHz(24);
  resized();
  timerCallback();
}
PrismEditor::~PrismEditor() {
  stopTimer();
  setLookAndFeel(nullptr);
}
void PrismEditor::filter() {
  presets.clear(dontSendNotification);
  filtered.clear();
  String query = search.getText().trim();
  for (int i = 0; i < bank.size(); ++i) {
    String title =
        bank[i]["category"].toString() + "  /  " + bank[i]["name"].toString();
    if (title.containsIgnoreCase(query)) {
      filtered.push_back(i);
      presets.addItem(title, int(filtered.size()));
    }
  }
  presets.setTextWhenNothingSelected(filtered.empty() ? "No matching sounds"
                                                      : "Choose a sound");
}
void PrismEditor::resized() {
  auto r = getLocalBounds().reduced(24);
  r.removeFromTop(52);
  auto browser = r.removeFromTop(32);
  search.setBounds(
      browser.removeFromLeft(int(browser.getWidth() * .29f)).reduced(0, 1));
  browser.removeFromLeft(10);
  previous.setBounds(browser.removeFromLeft(32));
  next.setBounds(browser.removeFromRight(32));
  presets.setBounds(browser.reduced(6, 0));
  r.removeFromTop(12);
  scopeArea = r.removeFromTop(136);
  r.removeFromTop(12);
  auto toolbar = r.removeFromTop(30);
  page.setBounds(toolbar.removeFromLeft(245));
  toolbar.removeFromLeft(8);
  arps.setBounds(toolbar.removeFromRight(220));
  r.removeFromTop(10);
  auto bottom = r.removeFromBottom(32);
  for (auto *b : {&mutate, &undoButton, &ab, &swap, &load, &save, &panicButton})
    b->setBounds(bottom
                     .removeFromLeft(bottom.getWidth() / (b == &mutate       ? 7
                                                          : b == &undoButton ? 6
                                                          : b == &ab         ? 5
                                                          : b == &swap       ? 4
                                                          : b == &load       ? 3
                                                          : b == &save ? 2
                                                                       : 1))
                     .reduced(3, 0));
  r.removeFromBottom(10);
  keyboard.setBounds(r.removeFromBottom(72));
  r.removeFromBottom(10);
  std::vector<int> ids;
  if (page.getSelectedId() == 1)
    ids = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15};
  else if (page.getSelectedId() == 2)
    ids = {16, 17, 18, 19, 21, 22, 23, 24, 25};
  else
    ids = {20, 26, 27, 28, 29, 30, 31};
  for (int i = 0; i < parameterCount; ++i) {
    sliders[size_t(i)].setVisible(false);
    labels[size_t(i)].setVisible(false);
  }
  int columns = page.getSelectedId() == 1   ? 8
                : page.getSelectedId() == 2 ? 5
                                            : 4;
  int rows = (int(ids.size()) + columns - 1) / columns;
  for (size_t j = 0; j < ids.size(); ++j) {
    int i = ids[j];
    auto cell =
        Rectangle<int>(r.getX() + int(j % columns) * r.getWidth() / columns,
                       r.getY() + int(j / columns) * r.getHeight() / rows,
                       r.getWidth() / columns, r.getHeight() / rows)
            .reduced(4);
    labels[size_t(i)].setBounds(cell.removeFromTop(20));
    sliders[size_t(i)].setBounds(cell);
    sliders[size_t(i)].setVisible(true);
    labels[size_t(i)].setVisible(true);
  }
}
void PrismEditor::timerCallback() {
  int write = processor.scopeWrite.load(std::memory_order_acquire);
  for (int i = 0; i < 512; ++i) {
    waveform[size_t(i)] = processor.scope[size_t((write + i) % 512)].load(
        std::memory_order_relaxed);
    fftData[size_t(i)] =
        waveform[size_t(i)] *
        (.5f - .5f * std::cos(MathConstants<float>::twoPi * i / 511.f));
  }
  std::fill(fftData.begin() + 512, fftData.end(), 0);
  fft.performFrequencyOnlyForwardTransform(fftData.data());
  repaint(scopeArea);
}
void PrismEditor::paint(Graphics &g) {
  g.fillAll(Colour(0xff10151f));
  g.setColour(Colour(0xff79efca));
  g.setFont(28);
  g.drawText("PRISM", 24, 18, 160, 34, Justification::centredLeft);
  g.setColour(Colour(0xff9bacc1));
  g.setFont(13);
  g.drawText("ANHARMONIC  /  POLYPHONIC SOUND LAB", 185, 21, 460, 30,
             Justification::centredLeft);
  g.drawText("32 VOICES  /  STEREO  /  VST3", getWidth() - 310, 21, 286, 30,
             Justification::centredRight);
  auto a = scopeArea.toFloat();
  g.setColour(Colour(0xff192433));
  g.fillRoundedRectangle(a, 10);
  auto wave = a.reduced(14);
  auto spectrum = wave.removeFromRight(wave.getWidth() * .34f);
  wave.removeFromRight(20);
  g.setFont(11);
  g.setColour(Colour(0xff9bacc1));
  g.drawText("OUTPUT WAVEFORM", wave.removeFromTop(20),
             Justification::centredLeft);
  g.drawText("SPECTRUM", spectrum.removeFromTop(20),
             Justification::centredLeft);
  g.setColour(Colour(0xff2c394b));
  for (int i = 1; i < 4; ++i) {
    float y = wave.getY() + wave.getHeight() * i / 4;
    g.drawHorizontalLine(int(y), wave.getX(), wave.getRight());
  }
  Path path;
  for (int i = 0; i < 512; ++i) {
    float x = wave.getX() + wave.getWidth() * i / 511.f,
          y = wave.getCentreY() - waveform[size_t(i)] * wave.getHeight() * .46f;
    if (i == 0)
      path.startNewSubPath(x, y);
    else
      path.lineTo(x, y);
  }
  g.setColour(Colour(0xff79efca));
  g.strokePath(path, PathStrokeType(1.7f));
  for (int i = 0; i < 48; ++i) {
    int bin = std::clamp(int(std::pow(255., i / 47.)), 1, 255);
    float db = Decibels::gainToDecibels(fftData[size_t(bin)] / 128.f, -72.f);
    float h = std::clamp((db + 72) / 72, 0.f, 1.f) * spectrum.getHeight();
    g.setColour(Colour(0xffa38bff));
    g.fillRect(spectrum.getX() + i * spectrum.getWidth() / 48,
               spectrum.getBottom() - h, spectrum.getWidth() / 48 - 2, h);
  }
}
void PrismEditor::choose(bool writing) {
  chooser = std::make_unique<FileChooser>(writing ? "Save Prism sound"
                                                  : "Load Prism sound",
                                          File{}, "*.prism.json", true);
  auto safe = Component::SafePointer<PrismEditor>(this);
  chooser->launchAsync(
      writing ? FileBrowserComponent::saveMode |
                    FileBrowserComponent::canSelectFiles |
                    FileBrowserComponent::warnAboutOverwriting
              : FileBrowserComponent::openMode |
                    FileBrowserComponent::canSelectFiles,
      [safe, writing](const FileChooser &c) {
        if (!safe)
          return;
        auto file = c.getResult();
        if (file == File{})
          return;
        if (writing) {
          auto data = JSON::toString(safe->processor.exportSound(), true);
          if (!file.replaceWithText(data))
            AlertWindow::showMessageBoxAsync(
                MessageBoxIconType::WarningIcon, "Save failed",
                "The sound file could not be written.");
        } else {
          if (file.getSize() > 1024 * 1024) {
            AlertWindow::showMessageBoxAsync(
                MessageBoxIconType::WarningIcon, "Load failed",
                "This file exceeds the sound preset size limit.");
            return;
          }
          auto data = JSON::parse(file);
          if (data["format"].toString() != "anharmonic-prism" ||
              int(data["version"]) != 1 || !data["patch"].isObject() ||
              data["patch"]["sample_source"].toString().isNotEmpty()) {
            AlertWindow::showMessageBoxAsync(
                MessageBoxIconType::WarningIcon, "Load failed",
                "Choose a Prism synth sound, version 1.");
            return;
          }
          safe->processor.loadSound(data);
        }
      });
}
