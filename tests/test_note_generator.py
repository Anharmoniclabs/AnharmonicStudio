import pytest
from mpclab.note_generator import generate_notes, SCALES
from mpclab.model import Project
from mpclab.music import Note
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from tests.test_product_hardening_ui import window  # noqa: F401


@pytest.mark.parametrize("style", ["Chords", "Arpeggio", "Bass", "Melody"])
@pytest.mark.parametrize("scale", list(SCALES))
def test_generated_phrases_are_bounded_repeatable_and_routed(style, scale):
    notes = generate_notes(16, 60, scale, style, 0.25, 12, pad=3)
    assert notes == generate_notes(16, 60, scale, style, 0.25, 12, pad=3)
    assert max(n.start + n.duration for n in notes) > 60
    assert all(
        0 <= n.start < n.start + n.duration <= 64 and 0 <= n.pitch <= 127 and n.pad == 3
        for n in notes
    )
    assert all((n.pitch - 60) % 12 in SCALES[scale] for n in notes)


def test_piano_length_duplicate_generation_and_undo_preserve_original(window):  # noqa: F811
    panel = window.piano_roll
    original = window.project.pattern()
    original.notes = [Note(60, 7, 1, 0.8)]
    panel.sync()
    panel.length_bars.setValue(8)
    assert original.bars == 8
    panel.length_bars.setValue(1)
    assert original.bars == 8
    panel.canvas.selected = {0}
    panel.canvas.duplicate_selected()
    assert original.notes[-1].start == 8
    before = (
        original.to_dict()
        if hasattr(original, "to_dict")
        else window.project.to_dict()["patterns"][0]
    )
    panel.generate_pattern(16, 60, "Minor", "Arpeggio", 0.25, 7)
    assert window.project.pattern().bars == 16
    assert window.project.pattern() is not original
    assert window.project.to_dict()["patterns"][0] == before
    loaded = Project.from_dict(window.project.to_dict())
    assert loaded.pattern().bars == 16
    assert max(n.start for n in loaded.pattern().notes) > 60
    window.undo()
    assert window.project.pattern().id == original.id


def test_select_marquee_paint_erase_and_shortcuts(window):  # noqa: F811
    panel = window.piano_roll
    pattern = window.project.pattern()
    pattern.notes = [Note(60, 1, 0.5, 0.8), Note(64, 2, 0.5, 0.8), Note(67, 3, 0.5, 0.8, pad=2)]
    panel.sync()
    canvas = panel.canvas
    QTest.keyClick(canvas, Qt.Key_A, Qt.ControlModifier)
    assert canvas.selected == {0, 1}
    QTest.keyClick(canvas, Qt.Key_D, Qt.ControlModifier)
    assert len(pattern.notes) == 5
    QTest.keyClick(canvas, Qt.Key_3, Qt.AltModifier)
    assert canvas.tool == "paint"
    position = QPointF(64 + 4 * canvas.px_per_beat, 28 + (127 - 65) * 20 + 10)
    canvas.paint_note(position)
    canvas.paint_note(position)
    assert sum(n.pitch == 65 and n.start == 4 for n in pattern.notes) == 1
    panel.set_tool("select")
    origin = QPointF(64 + 0.5 * canvas.px_per_beat, 28 + (127 - 66) * 20)
    end = QPointF(64 + 2.8 * canvas.px_per_beat, 28 + (127 - 59) * 20)
    for kind, pos, button, buttons in (
        (QEvent.MouseButtonPress, origin, Qt.LeftButton, Qt.LeftButton),
        (QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
        (QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton),
    ):
        QApplication.sendEvent(canvas, QMouseEvent(kind, pos, pos, button, buttons, Qt.NoModifier))
    assert 0 in canvas.selected and 1 in canvas.selected
    assert 2 not in canvas.selected


def test_long_generated_pattern_schedules_notes_past_two_bars(window):  # noqa: F811
    panel = window.piano_roll
    panel.generate_pattern(8, 60, "Major", "Melody", 0.5, 5)
    collected, _ = window.engine._collect(16.0, 20.0)
    assert collected, "Long pattern must reach the audio scheduler after bar four"


def test_legato_and_sticky_keyboard_survive_horizontal_scroll(window):  # noqa: F811
    panel = window.piano_roll
    pattern = window.project.pattern()
    pattern.bars = 16
    pattern.notes = [Note(60, 1, 0.25, 0.8), Note(64, 3, 0.25, 0.8)]
    panel.sync()
    panel.canvas.selected = {0, 1}
    QTest.keyClick(panel.canvas, Qt.Key_L, Qt.ControlModifier)
    assert pattern.notes[0].duration == 2
    assert pattern.notes[1].duration == 61
    panel.scroll.horizontalScrollBar().setValue(600)
    played = []
    window.play_selected_note = lambda pitch, velocity: played.append(pitch)
    window.release_selected_note = lambda pitch: None
    x = panel.scroll.horizontalScrollBar().value() + 30
    y = 28 + (127 - 60) * 20 + 10
    pos = QPointF(x, y)
    QApplication.sendEvent(
        panel.canvas,
        QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier),
    )
    assert played == [60]
    assert len(pattern.notes) == 2
    panel.canvas.paint_note(pos)
    assert len(pattern.notes) == 2
    QTest.mouseRelease(panel.canvas, Qt.LeftButton)
