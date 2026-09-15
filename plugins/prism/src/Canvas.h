// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "Processor.h"
class PrismCanvas : public juce::Component {
public:
  enum Kind { Morph, Filter, Envelope, Sequence };
  PrismCanvas(PrismProcessor &p, Kind k) : processor(p), kind(k) {
    setMouseCursor(juce::MouseCursor::CrosshairCursor);
    setTitle(k == Sequence   ? "Draw eight modulation steps"
             : k == Envelope ? "Drag envelope handles"
                             : "Drag sound shaping pad");
  }
  void setLayer(bool second) {
    layer = second ? 32 : 0;
    repaint();
  }
  void mouseDown(const juce::MouseEvent &e) override {
    processor.undo.beginNewTransaction("Visual sound edit");
    active =
        kind == Morph    ? std::vector<int>{54, 57}
        : kind == Filter ? std::vector<int>{layer + 12, layer + 13}
        : kind == Sequence
            ? std::vector<int>{61, 62, 63, 64, 65, 66, 67, 68}
            : std::vector<int>{layer + 8, layer + 9, layer + 10, layer + 11};
    for (int i : active)
      processor.state.getParameter(specs[i].id)->beginChangeGesture();
    handle = e.x < getWidth() * .35f ? 0 : e.x < getWidth() * .7f ? 1 : 2;
    if (kind == Sequence &&
        processor.state.getRawParameterValue("seq_depth")->load() == 0)
      processor.setValue(58, .5f);
    mouseDrag(e);
  }
  void mouseUp(const juce::MouseEvent &) override {
    for (int i : active)
      processor.state.getParameter(specs[i].id)->endChangeGesture();
    active.clear();
  }
  void mouseDrag(const juce::MouseEvent &e) override {
    float x =
        juce::jlimit(0.f, 1.f, (e.x - 16.f) / std::max(1, getWidth() - 32));
    float y = juce::jlimit(0.f, 1.f,
                           1 - (e.y - 30.f) / std::max(1, getHeight() - 48));
    auto set = [&](int i, float v) {
      processor.state.getParameter(specs[i].id)
          ->setValueNotifyingHost(juce::jlimit(0.f, 1.f, v));
    };
    if (kind == Morph) {
      set(54, x);
      set(57, y);
    }
    if (kind == Filter) {
      set(layer + 12, x);
      set(layer + 13, y);
    }
    if (kind == Sequence)
      set(61 + std::min(7, int(x * 8)), y);
    if (kind == Envelope) {
      if (handle == 0)
        set(layer + 8, x / .35f);
      if (handle == 1) {
        set(layer + 9, (x - .35f) / .35f);
        set(layer + 10, y);
      }
      if (handle == 2)
        set(layer + 11, (x - .7f) / .3f);
    }
    repaint();
  }
  void paint(juce::Graphics &g) override {
    using namespace juce;
    auto r = getLocalBounds().toFloat();
    g.setColour(Colour(0xff0a1220));
    g.fillRoundedRectangle(r, 8);
    g.setColour(Colour(0xff29364d));
    g.drawRoundedRectangle(r.reduced(.5f), 8, 1);
    g.setColour(Colour(0xffa2b3cb));
    g.setFont(11);
    g.drawText(kind == Morph      ? "MORPH  /  TONE x TEXTURE"
               : kind == Filter   ? "FILTER  /  CUTOFF x RESONANCE"
               : kind == Envelope ? "ENVELOPE  /  DRAG ATTACK, DECAY, RELEASE"
                                  : "STEP MOTION  /  DRAW YOUR RHYTHM",
               16, 8, getWidth() - 32, 18, Justification::centredLeft);
    auto area = r.reduced(16, 0).withTrimmedTop(30).withTrimmedBottom(18);
    g.setColour(Colour(0xff1c2a40));
    for (int i = 1; i < 8; ++i)
      g.drawVerticalLine(int(area.getX() + i * area.getWidth() / 8),
                         area.getY(), area.getBottom());
    for (int i = 1; i < 4; ++i)
      g.drawHorizontalLine(int(area.getY() + i * area.getHeight() / 4),
                           area.getX(), area.getRight());
    auto value = [&](int i) {
      return processor.state.getParameter(specs[i].id)->getValue();
    };
    if (kind == Sequence) {
      for (int i = 0; i < 8; ++i) {
        float h = value(61 + i) * area.getHeight();
        g.setColour(
            Colour(processor.motionStep.load() == i ? 0xff8af5d2 : 0xffa894f7));
        g.fillRoundedRectangle(area.getX() + i * area.getWidth() / 8 + 3,
                               area.getBottom() - h, area.getWidth() / 8 - 6,
                               std::max(2.f, h), 3);
      }
    } else if (kind == Envelope) {
      Point<float> points[] = {
          {area.getX(), area.getBottom()},
          {area.getX() + area.getWidth() * .35f * value(layer + 8),
           area.getY()},
          {area.getX() + area.getWidth() * (.35f + .35f * value(layer + 9)),
           area.getBottom() - area.getHeight() * value(layer + 10)},
          {area.getX() + area.getWidth() * (.7f + .3f * value(layer + 11)),
           area.getBottom()}};
      Path path;
      path.startNewSubPath(points[0]);
      for (int i = 1; i < 4; ++i)
        path.lineTo(points[i]);
      g.setColour(Colour(0xffa894f7));
      g.strokePath(path, PathStrokeType(2));
      for (int i = 1; i < 4; ++i)
        g.fillEllipse(points[i].x - 4, points[i].y - 4, 8, 8);
    } else {
      float x = area.getX() +
                area.getWidth() * value(kind == Morph ? 54 : layer + 12);
      float y = area.getBottom() -
                area.getHeight() * value(kind == Morph ? 57 : layer + 13);
      g.setColour(Colour(0xff83ead2));
      g.drawVerticalLine(int(x), area.getY(), area.getBottom());
      g.drawHorizontalLine(int(y), area.getX(), area.getRight());
      g.setColour(Colour(0x3383ead2));
      g.fillEllipse(x - 18, y - 18, 36, 36);
      g.setColour(Colour(0xff83ead2));
      g.fillEllipse(x - 5, y - 5, 10, 10);
    }
  }

private:
  PrismProcessor &processor;
  Kind kind;
  int layer = 0, handle = 0;
  std::vector<int> active;
};
