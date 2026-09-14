"""Precision controls must change musical coordinates, not just their labels."""

from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

from mpclab.model import Project
from mpclab.music import Note
from mpclab.ui.color_picker import ColorWheel
from mpclab.ui.piano_roll import PianoRollPanel
from mpclab.ui.waveform import WaveformView


def test_cut_cursor_bypasses_grid_and_keyboard_nudges_one_sample():
    view = WaveformView()
    view.set_clip(np.zeros((48000, 2)), np.zeros((100, 2)), 1.0, [], 120)
    view.snap_mode = "beat grid"
    view.cut_mode = True
    view.set_cut_cursor(0.123)
    QTest.keyClick(view, Qt.Key_Right, Qt.AltModifier)
    assert view.cut_cursor == pytest.approx(0.123 + 1 / 48000)
    QTest.keyClick(view, Qt.Key_M)
    assert view.markers[-1] == pytest.approx(view.cut_cursor, abs=1e-8)
    view.set_cut_cursor(0.41, snap=True)
    assert view.cut_cursor == 0.5


def test_color_wheel_keys_and_pointer_choose_consistent_hue():
    wheel = ColorWheel(QColor("#ff0000"))
    wheel.resize(220, 220)
    selected = []
    wheel.colorChanged.connect(lambda c: selected.append(c.hsvHue()))
    wheel._choose(QPointF(wheel.rect().center().x(), 0))
    assert selected[-1] == 90
    QTest.keyClick(wheel, Qt.Key_Right, Qt.ShiftModifier)
    assert selected[-1] == 105
    # The top of the rendered wheel must be the same hue as the pointer math.
    image = wheel.grab().toImage()
    actual = image.pixelColor(wheel.width() // 2, 25).hsvHue()
    assert abs(actual - 90) < 5


def test_note_start_zoom_and_triplet_grid_keep_musical_coordinates():
    project = Project()
    app = SimpleNamespace(
        project=project,
        engine=SimpleNamespace(beat=0, playing=False),
        snapshot=lambda: None,
        _set_dirty=lambda dirty: None,
    )
    panel = PianoRollPanel(app)
    note = Note(60, 1.0, 0.5, 0.8)
    project.pattern().notes.append(note)
    panel.canvas.selected = {0}
    panel.sync_selection()
    panel.start.setValue(1.0625)
    assert note.start == 1.0625
    panel.snap.setCurrentIndex(panel.snap.findData(1 / 6))
    assert panel.canvas.snap(0.49) == pytest.approx(0.5)
    panel.zoom_time(2)
    assert note.start == 1.0625
    assert panel.canvas.rect_for(note).x() == pytest.approx(64 + 1.0625 * 176)
    panel.pitch.setValue(127)
    assert note.pitch == 127
    panel.pitch.setValue(0)
    assert note.pitch == 0
    panel.canvas.grab()
