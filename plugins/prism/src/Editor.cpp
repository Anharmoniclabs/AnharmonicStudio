// SPDX-License-Identifier: GPL-3.0-or-later
#include "Editor.h"
using namespace juce;
PrismEditor::PrismEditor(PrismProcessor &p)
    : AudioProcessorEditor(p), processor(p), browser("Sound browser", this),
      keyboard(p.keyboard, MidiKeyboardComponent::horizontalKeyboard),
      morph(p, PrismCanvas::Morph), filterPad(p, PrismCanvas::Filter),
      envelope(p, PrismCanvas::Envelope), sequence(p, PrismCanvas::Sequence) {
  look.setColour(ResizableWindow::backgroundColourId, Colour(0xff101725));
  look.setColour(Slider::rotarySliderFillColourId, Colour(0xff89edd2));
  look.setColour(Slider::rotarySliderOutlineColourId, Colour(0xff29384b));
  look.setColour(Slider::textBoxOutlineColourId, Colours::transparentBlack);
  look.setColour(TextButton::buttonColourId, Colour(0xff223047));
  look.setColour(TextButton::buttonOnColourId, Colour(0xff594c87));
  look.setColour(ComboBox::backgroundColourId, Colour(0xff172338));
  look.setColour(ListBox::backgroundColourId, Colour(0xff101b2c));
  setLookAndFeel(&look);
  setResizable(true, true);
  setResizeLimits(1040, 840, 1800, 1280);
  setSize(1240, 940);
  bank = JSON::parse(factoryJSON);
  arpBank = JSON::parse(arpJSON);
  auto performances = JSON::parse(performanceJSON);
  for (int i = 0; i < performances.size(); ++i)
    bank.append(performances[i]);
  starred.addTokens(processor.state.state.getProperty("favorites").toString(),
                    "|", "");
  for (Component *c : std::initializer_list<Component *>{
           &search,      &category,     &arps,     &browser,
           &soundTitle,  &libraryTitle, &favorite, &favoritesOnly,
           &layerA,      &layerB,       &mutate,   &undoButton,
           &ab,          &swap,         &load,     &save,
           &panicButton, &keyboard,     &morph,    &filterPad,
           &envelope,    &sequence})
    addAndMakeVisible(c);
  libraryTitle.setText("SOUNDS / DOUBLE-CLICK TO LOAD", dontSendNotification);
  libraryTitle.setColour(Label::textColourId, Colour(0xffa894f7));
  soundTitle.setText("INIT  /  YOUR NEXT SOUND STARTS HERE",
                     dontSendNotification);
  soundTitle.setFont(Font(18, Font::bold));
  search.setTextToShowWhenEmpty("Search sounds and categories...",
                                Colour(0xff98abc6));
  search.onTextChange = [this] { filter(); };
  category.addItem("All categories", 1);
  StringArray categories;
  for (int i = 0; i < bank.size(); ++i)
    categories.addIfNotAlreadyThere(bank[i]["category"].toString());
  for (auto &c : categories)
    category.addItem(c, category.getNumItems() + 1);
  category.setSelectedId(1, dontSendNotification);
  category.onChange = [this] { filter(); };
  favoritesOnly.setClickingTogglesState(true);
  favoritesOnly.onClick = [this] { filter(); };
  favorite.onClick = [this] {
    if (selected < 0)
      return;
    auto name = bank[selected]["name"].toString();
    if (starred.contains(name))
      starred.removeString(name);
    else
      starred.add(name);
    processor.state.state.setProperty("favorites", starred.joinIntoString("|"),
                                      nullptr);
    filter();
  };
  layerA.onClick = [this] {
    if (selected >= 0)
      processor.loadLayer(bank[selected], false);
  };
  layerB.onClick = [this] {
    if (selected >= 0)
      processor.loadLayer(bank[selected], true);
  };
  browser.setRowHeight(46);
  StringArray titles{"PERFORM", "LAYER A", "LAYER B", "MOTION", "EFFECTS"};
  for (int i = 0; i < 5; ++i) {
    auto &b = pages[i];
    b.setButtonText(titles[i]);
    b.setClickingTogglesState(true);
    b.onClick = [this, i] {
      page = i;
      resized();
      repaint();
    };
    addAndMakeVisible(b);
  }
  arps.setTextWhenNothingSelected("Arp recipes");
  for (int i = 0; i < arpBank.size(); ++i)
    arps.addItem(arpBank[i]["name"].toString(), i + 1);
  arps.onChange = [this] {
    processor.undo.beginNewTransaction("Arp recipe");
    processor.loadArp(arpBank[arps.getSelectedId() - 1]);
  };
  mutate.onClick = [this] {
    processor.undo.beginNewTransaction("Mutate tone");
    for (int i : {2, 4, 5, 12, 13, 14, 16, 34, 36, 44, 45, 48}) {
      auto *v = processor.state.getParameter(specs[i].id);
      processor.setValue(
          i, v->convertFrom0to1(std::clamp(
                 v->getValue() +
                     (Random::getSystemRandom().nextFloat() - .5f) * .24f,
                 0.f, 1.f)));
    }
  };
  undoButton.onClick = [this] { processor.undo.undo(); };
  ab.onClick = [this] {
    snapshot = processor.exportSound();
    ab.setButtonText("A/B stored");
  };
  swap.onClick = [this] {
    if (snapshot.isObject()) {
      auto previous = processor.exportSound();
      processor.loadSound(snapshot);
      snapshot = previous;
    }
  };
  save.onClick = [this] { choose(true); };
  load.onClick = [this] { choose(false); };
  panicButton.onClick = [this] {
    processor.panic.store(true);
    processor.keyboard.allNotesOff(0);
  };
  for (int i = 0; i < parameterCount; ++i) {
    auto &s = sliders[i];
    s.setSliderStyle(Slider::RotaryHorizontalVerticalDrag);
    s.setTextBoxStyle(Slider::TextBoxBelow, false, 88, 20);
    s.setName(specs[i].label);
    s.setTitle(specs[i].label);
    s.setDoubleClickReturnValue(true, specs[i].initial);
    s.onDragStart = [this] {
      processor.undo.beginNewTransaction("Shape sound");
    };
    if (i == 0 || i == 1 || i == 32 || i == 33)
      s.textFromValueFunction = [](double v) {
        return StringArray{"Saw", "Sine", "Triangle",
                           "Pulse"}[std::clamp(int(v), 0, 3)];
      };
    if (i == 21)
      s.textFromValueFunction = [](double v) {
        return String(v > .5 ? "On" : "Off");
      };
    if (i == 22 || i == 28 || i == 59)
      s.textFromValueFunction = [](double v) {
        return StringArray{
            "1/32", "1/8 T", "1/16", "1/8", "1/8 D", "1/4"}[std::clamp(int(v),
                                                                       0, 5)];
      };
    if (i == 23)
      s.textFromValueFunction = [](double v) {
        return StringArray{"Up", "Down", "Up/down",
                           "Random"}[std::clamp(int(v), 0, 3)];
      };
    if (i == 60 || i == 71)
      s.textFromValueFunction = [](double v) {
        return StringArray{"Cutoff", "Detune", "Level", "Pan",
                           "Osc blend"}[std::clamp(int(v), 0, 4)];
      };
    labels[i].setText(specs[i].label, dontSendNotification);
    labels[i].setJustificationType(Justification::centred);
    addAndMakeVisible(s);
    addAndMakeVisible(labels[i]);
    attachments[i] =
        std::make_unique<AudioProcessorValueTreeState::SliderAttachment>(
            processor.state, specs[i].id, s);
    s.setTextBoxIsEditable(specs[i].step == 0);
  }
  keyboard.setAvailableRange(24, 108);
  keyboard.setLowestVisibleKey(48);
  keyboard.setKeyWidth(24);
  filter();
  startTimerHz(24);
  resized();
  timerCallback();
}
PrismEditor::~PrismEditor() {
  stopTimer();
  browser.setModel(nullptr);
  setLookAndFeel(nullptr);
}
void PrismEditor::filter() {
  filtered.clear();
  auto query = search.getText().trim();
  for (int i = 0; i < bank.size(); ++i) {
    auto name = bank[i]["name"].toString(),
         cat = bank[i]["category"].toString();
    if ((name + " " + cat).containsIgnoreCase(query) &&
        (category.getSelectedId() == 1 || category.getText() == cat) &&
        (!favoritesOnly.getToggleState() || starred.contains(name)))
      filtered.push_back(i);
  }
  browser.updateContent();
  browser.repaint();
}
void PrismEditor::paintListBoxItem(int row, Graphics &g, int width, int height,
                                   bool selectedRow) {
  if (row < 0 || row >= int(filtered.size()))
    return;
  auto item = bank[filtered[row]];
  if (selectedRow) {
    g.setColour(Colour(0xff303552));
    g.fillRoundedRectangle(3, 2, float(width - 6), float(height - 4), 5);
  }
  g.setColour(Colour(0xffeef3ff));
  g.setFont(13);
  g.drawText((starred.contains(item["name"].toString()) ? "* " : "") +
                 item["name"].toString(),
             12, 5, width - 20, 19, Justification::centredLeft);
  g.setColour(Colour(0xff8fa4c4));
  g.setFont(10);
  g.drawText(item["category"].toString(), 12, 25, width - 20, 15,
             Justification::centredLeft);
}
void PrismEditor::selectedRowsChanged(int row) {
  if (row < 0 || row >= int(filtered.size()))
    return;
  selected = filtered[row];
}
void PrismEditor::listBoxItemDoubleClicked(int row, const MouseEvent &) {
  returnKeyPressed(row);
}
void PrismEditor::returnKeyPressed(int row) {
  if (row < 0 || row >= int(filtered.size()))
    return;
  selected = filtered[row];
  processor.loadSound(bank[selected]);
  soundTitle.setText(bank[selected]["name"].toString(), dontSendNotification);
}
void PrismEditor::resized() {
  auto r = getLocalBounds().reduced(20);
  r.removeFromTop(52);
  browserArea = r.removeFromLeft(222);
  r.removeFromLeft(16);
  auto left = browserArea;
  libraryTitle.setBounds(left.removeFromTop(28));
  search.setBounds(left.removeFromTop(30));
  left.removeFromTop(8);
  category.setBounds(left.removeFromTop(28));
  left.removeFromTop(8);
  auto stars = left.removeFromTop(28);
  favoritesOnly.setBounds(stars.removeFromLeft(100));
  favorite.setBounds(stars.reduced(4, 0));
  left.removeFromTop(8);
  auto layer = left.removeFromBottom(32);
  layerA.setBounds(layer.removeFromLeft(108));
  layerB.setBounds(layer.reduced(3, 0));
  left.removeFromBottom(8);
  browser.setBounds(left);
  auto footer = r.removeFromBottom(30);
  for (auto *b :
       {&mutate, &undoButton, &ab, &swap, &load, &save, &panicButton}) {
    int width = r.getWidth() / 7;
    b->setBounds(footer.removeFromLeft(width).reduced(3, 0));
  }
  r.removeFromBottom(10);
  keyboard.setBounds(r.removeFromBottom(70));
  r.removeFromBottom(12);
  soundTitle.setBounds(r.removeFromTop(30));
  auto macro = r.removeFromTop(105);
  int cw = macro.getWidth() / 5;
  for (int i = 0; i < parameterCount; ++i) {
    sliders[i].setVisible(false);
    labels[i].setVisible(false);
  }
  auto place = [&](int i, Rectangle<int> cell) {
    labels[i].setBounds(cell.removeFromTop(19));
    sliders[i].setBounds(cell);
    labels[i].setVisible(true);
    sliders[i].setVisible(true);
  };
  for (int i : {53, 54, 55, 56, 57})
    place(i, macro.removeFromLeft(cw).reduced(3, 0));
  r.removeFromTop(10);
  auto nav = r.removeFromTop(32);
  for (int i = 0; i < 5; ++i) {
    pages[i].setBounds(nav.removeFromLeft(r.getWidth() / 5).reduced(3, 0));
    pages[i].setToggleState(page == i, dontSendNotification);
  }
  r.removeFromTop(12);
  auto display = r.removeFromTop(146);
  const auto visualArea = display;
  auto first = display.removeFromLeft(display.getWidth() / 2);
  first.removeFromRight(6);
  display.removeFromLeft(6);
  morph.setVisible(page == 0);
  filterPad.setVisible(page == 1 || page == 2);
  envelope.setVisible(page == 1 || page == 2);
  sequence.setVisible(page == 0 || page == 3);
  morph.setBounds(first);
  filterPad.setBounds(first);
  envelope.setBounds(display);
  sequence.setBounds(page == 3 ? first : display);
  filterPad.setLayer(page == 2);
  envelope.setLayer(page == 2);
  scopeArea = (page == 3   ? display
               : page == 4 ? r.withHeight(146)
                           : Rectangle<int>{});
  if (page == 4)
    scopeArea = display.withX(first.getX())
                    .withWidth(first.getWidth() + display.getWidth() + 12);
  if (page == 0) {
    auto visual = visualArea;
    int width = visual.getWidth() / 3;
    morph.setBounds(visual.removeFromLeft(width).reduced(4, 0));
    sequence.setBounds(visual.removeFromLeft(width).reduced(4, 0));
    scopeArea = visual.reduced(4, 0);
  }
  r.removeFromTop(10);
  arps.setVisible(page == 3);
  if (page == 3) {
    arps.setBounds(r.removeFromTop(28).removeFromRight(220));
    r.removeFromTop(6);
  }
  std::vector<int> ids;
  if (page == 0)
    ids = {12, 13, 8, 11, 58, 69, 70, 20};
  if (page == 1)
    for (int i = 0; i < 21; ++i)
      ids.push_back(i);
  if (page == 2)
    for (int i = 32; i < 53; ++i)
      ids.push_back(i);
  if (page == 3)
    ids = {17, 18, 19, 21, 22, 23, 24, 25, 58, 59, 60, 69, 70, 71};
  if (page == 4)
    ids = {26, 27, 28, 29, 30, 31, 72, 73, 74, 75};
  int columns = page == 1 || page == 2 || page == 3 ? 7 : 5;
  int rows = (int(ids.size()) + columns - 1) / columns;
  for (size_t j = 0; j < ids.size(); ++j)
    place(ids[j],
          Rectangle<int>(r.getX() + int(j % columns) * r.getWidth() / columns,
                         r.getY() + int(j / columns) * r.getHeight() / rows,
                         r.getWidth() / columns, r.getHeight() / rows)
              .reduced(3));
}
void PrismEditor::timerCallback() {
  int write = processor.scopeWrite.load(std::memory_order_acquire);
  for (int i = 0; i < 512; ++i) {
    waveform[i] = processor.scope[(write + i) % 512].load();
    fftData[i] =
        waveform[i] *
        (.5f - .5f * std::cos(MathConstants<float>::twoPi * i / 511.f));
  }
  std::fill(fftData.begin() + 512, fftData.end(), 0);
  fft.performFrequencyOnlyForwardTransform(fftData.data());
  morph.repaint();
  filterPad.repaint();
  envelope.repaint();
  sequence.repaint();
  repaint(scopeArea);
}
void PrismEditor::paint(Graphics &g) {
  g.fillAll(Colour(0xff101725));
  g.setColour(Colour(0xff89edd2));
  g.setFont(Font(29, Font::bold));
  g.drawText("PRISM", 24, 15, 170, 35, Justification::centredLeft);
  g.setColour(Colour(0xffa894f7));
  g.setFont(12);
  g.drawText("ANHARMONIC  /  LAYERED SOUND WORKSTATION", 185, 20, 500, 30,
             Justification::centredLeft);
  g.setColour(Colour(0xff8fa4c4));
  g.drawText("2 LAYERS   /   64 VOICES   /   5 FX", getWidth() - 315, 20, 290,
             30, Justification::centredRight);
  if (scopeArea.isEmpty())
    return;
  auto a = scopeArea.toFloat();
  g.setColour(Colour(0xff0a1220));
  g.fillRoundedRectangle(a, 8);
  auto wave = a.reduced(14);
  auto spectrum = wave.removeFromRight(wave.getWidth() * .34f);
  wave.removeFromRight(15);
  g.setFont(11);
  g.setColour(Colour(0xff8fa4c4));
  g.drawText("LIVE OUTPUT", wave.removeFromTop(20), Justification::centredLeft);
  g.drawText("SPECTRUM", spectrum.removeFromTop(20),
             Justification::centredLeft);
  Path path;
  for (int i = 0; i < 512; ++i) {
    float x = wave.getX() + wave.getWidth() * i / 511.f,
          y = wave.getCentreY() - waveform[i] * wave.getHeight() * .46f;
    if (i == 0)
      path.startNewSubPath(x, y);
    else
      path.lineTo(x, y);
  }
  g.setColour(Colour(0xff89edd2));
  g.strokePath(path, PathStrokeType(1.7f));
  for (int i = 0; i < 48; ++i) {
    int bin = std::clamp(int(std::pow(255., i / 47.)), 1, 255);
    float db = Decibels::gainToDecibels(fftData[bin] / 128.f, -72.f),
          h = std::clamp((db + 72) / 72, 0.f, 1.f) * spectrum.getHeight();
    g.setColour(Colour(0xffa894f7));
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
