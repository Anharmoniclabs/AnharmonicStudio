"""Precise sampler positioning through the real inspector and project history."""

import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QSlider

from mpclab.engine import Engine
from mpclab.ui import main_window
from mpclab.ui.padgrid import PadPanControl
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self, device=None: None)
    instance = main_window.MainWindow(tmp_path, restore_session=False)
    instance._ui_timer.stop()
    instance._autosave_timer.stop()
    t = np.arange(96_000) / 48_000
    tone = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    clip = instance.library.add_audio(np.column_stack((tone, tone)), "Synthetic A4")
    for index in (0, 19):
        pad = instance.project.pads[index]
        pad.sample_id, pad.end = clip.id, clip.duration
    instance.select_pad(19)
    instance._set_dirty(False)
    try:
        yield instance
    finally:
        instance._dirty = False
        instance.close()


def pan_box(window):
    box = window.pad_inspector.findChild(QDoubleSpinBox, "padPan")
    assert box is not None, "Pad pan needs a direct numeric editor"
    return box


def type_pan(window, text):
    box = pan_box(window)
    box.selectAll()
    QTest.keyClicks(box, text)
    QTest.keyClick(box, Qt.Key_Return)


def test_pan_slider_edit_is_one_undo_step(window):
    slider = window.pad_inspector.findChildren(QSlider)[1]
    slider.setSliderDown(True)
    for value in (slider.value() + 10, slider.value() + 20, slider.value() + 30):
        slider.setValue(value)
    slider.setSliderDown(False)
    edited = window.project.pads[19].pan
    assert edited != 0
    assert len(window._undo) == 1
    window.undo()
    assert window.project.pads[19].pan == 0
    window.redo()
    assert window.project.pads[19].pan == edited


def test_percent_entry_commits_once_and_targets_selected_pad(window):
    box = pan_box(window)
    box.selectAll()
    QTest.keyClicks(box, "7.3")
    assert window.project.pads[19].pan == 0
    assert not window._undo
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].pan == 0.073
    assert window.project.pads[0].pan == 0
    assert len(window._undo) == 1
    assert window._dirty
    window.undo()
    assert pan_box(window).value() == 0
    window.redo()
    assert pan_box(window).value() == 7.3


def test_pan_keyboard_steps_and_limits(window):
    box = pan_box(window)
    QTest.keyClick(box, Qt.Key_Up)
    assert window.project.pads[19].pan == 0.001
    QTest.keyClick(box, Qt.Key_Down)
    assert window.project.pads[19].pan == 0
    slider = window.pad_inspector.findChild(PadPanControl).slider
    QTest.keyClick(slider, Qt.Key_Right)
    assert window.project.pads[19].pan == 0.001
    QTest.keyClick(slider, Qt.Key_PageUp)
    assert window.project.pads[19].pan == pytest.approx(0.101, abs=1e-15)
    type_pan(window, "100.0")
    history_size = len(window._undo)
    QTest.keyClick(box, Qt.Key_Up)
    assert window.project.pads[19].pan == 1
    assert len(window._undo) == history_size
    type_pan(window, "-100.0")
    history_size = len(window._undo)
    QTest.keyClick(box, Qt.Key_Down)
    assert window.project.pads[19].pan == -1
    assert len(window._undo) == history_size


def test_pan_escape_cancels_typing_without_stopping_transport(window, monkeypatch):
    type_pan(window, "-3.2")
    stopped = []
    monkeypatch.setattr(window, "stop_all", lambda: stopped.append(True))
    box = pan_box(window)
    box.selectAll()
    QTest.keyClicks(box, "12.7")
    QTest.keyClick(box, Qt.Key_Escape)
    assert box.cleanText() == "-3.2"
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].pan == -0.032
    assert len(window._undo) == 1
    assert not stopped


def test_pan_focus_out_commits_the_complete_value(window):
    box = pan_box(window)
    box.selectAll()
    QTest.keyClicks(box, "-0.7")
    QApplication.sendEvent(box, QFocusEvent(QEvent.FocusOut, Qt.TabFocusReason))
    assert window.project.pads[19].pan == pytest.approx(-0.007, abs=1e-15)
    assert len(window._undo) == 1


def test_pan_rebuild_discards_pending_text_and_preserves_saved_precision(window):
    window.project.pads[19].pan = 0.314159
    window.pad_inspector.rebuild()
    assert window.project.pads[19].pan == 0.314159
    assert not window._undo and not window._dirty
    old_box = pan_box(window)
    old_box.selectAll()
    QTest.keyClicks(old_box, "12.7")
    window.select_pad(0)
    QApplication.sendEvent(old_box, QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
    assert window.project.pads[19].pan == 0.314159
    assert window.project.pads[0].pan == 0
    assert not window._undo and not window._dirty
    window.select_pad(1)
    assert window.pad_inspector.findChild(PadPanControl) is None


def test_pan_reset_and_no_op_edits_preserve_redo(window):
    type_pan(window, "7.3")
    control = window.pad_inspector.findChild(PadPanControl)
    control.reset_action.trigger()
    assert window.project.pads[19].pan == 0
    assert pan_box(window).value() == 0
    assert len(window._undo) == 2
    window.undo()
    assert window.project.pads[19].pan == 0.073
    window.undo()
    control = window.pad_inspector.findChild(PadPanControl)
    control.reset_action.trigger()
    control.slider.setSliderDown(True)
    control.slider.setSliderDown(False)
    type_pan(window, "0.0")
    assert not window._undo
    assert len(window._redo) == 2
    window.redo()
    assert window.project.pads[19].pan == 0.073


def test_pan_shortcuts_with_numeric_focus(window):
    # This is the test's disposable offscreen widget, never a desktop window.
    assert QApplication.platformName() == "offscreen"
    window.pad_side.show()
    window.show()
    box = pan_box(window)
    box.setFocus()
    QApplication.processEvents()
    assert QApplication.focusWidget() is box
    type_pan(window, "7.3")
    QTest.keyClick(box, Qt.Key_0, Qt.AltModifier)
    assert window.project.pads[19].pan == 0
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier)
    assert window.project.pads[19].pan == 0.073
    box = pan_box(window)
    box.setFocus()
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert window.project.pads[19].pan == 0


def test_pan_undo_redo_during_typing_stays_in_the_field(window):
    assert QApplication.platformName() == "offscreen"
    window.pad_side.show()
    window.show()
    box = pan_box(window)
    box.setFocus()
    QApplication.processEvents()
    type_pan(window, "7.3")
    box.selectAll()
    QTest.keyClicks(box, "12.7")
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier)
    assert window.project.pads[19].pan == 0.073
    assert len(window._undo) == 1
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert box.cleanText() == "12.7"
    assert window.project.pads[19].pan == 0.073
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].pan == 0.127
    assert len(window._undo) == 2


@pytest.mark.parametrize("invalid", ["", "-", "101.0", "nan"])
def test_invalid_pan_does_not_change_project_or_history(window, invalid):
    box = pan_box(window)
    box.lineEdit().setText(invalid)
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].pan == 0
    assert not window._undo
    assert not window._dirty


def test_percent_pan_and_history_survive_save_and_reopen(window):
    type_pan(window, "12.7")
    path = window.projects_dir / "percent-tuning.json"
    assert window._save_project_to(path)
    reopened = main_window.MainWindow(window.root, restore_session=False)
    try:
        assert reopened.load_project_path(path)
        reopened.select_pad(19)
        assert pan_box(reopened).value() == 12.7
        assert reopened.project.pads[19].pan == 0.127
        assert len(reopened._undo) == 1
        reopened.undo()
        assert reopened.project.pads[19].pan == 0
        reopened.redo()
        assert reopened.project.pads[19].pan == 0.127
    finally:
        reopened._dirty = False
        reopened.close()


@pytest.mark.parametrize("percent", [-100, -37.3, 0, 12.7, 100])
def test_entered_pan_in_callback_and_export(window, tmp_path, percent):
    import soundfile as sf

    from mpclab.export import render_export
    from mpclab.model import Project

    type_pan(window, str(percent))
    engine = Engine(window.library, blocksize=512)
    engine.project = Project.from_dict(window.project.to_dict())
    engine.preload_project_audio()
    engine.trigger_pad(19)
    chunks = []
    for _ in range(48):
        block = np.zeros((512, 2), dtype=np.float32)
        engine._callback(block, 512, None, False)
        chunks.append(block)
    engine.project.pattern().bars = 1
    engine.project.pattern().steps = {19: {0: 1.0}}
    destination = tmp_path / "panned.wav"
    render_export(
        engine.project, window.library, destination, mode="pattern", tail=0, subtype="FLOAT"
    )
    exported, sr = sf.read(destination, dtype="float32", always_2d=True)
    assert sr == 48000
    angle = (percent / 100 + 1) * np.pi / 4
    expected = np.array([np.cos(angle), np.sin(angle)])
    directions = []
    for signal in (np.concatenate(chunks), exported):
        assert np.isfinite(signal).all()
        rms = np.sqrt(np.mean(signal[4800:19200].astype(np.float64) ** 2, axis=0))
        assert np.linalg.norm(rms) > 0.01
        direction = rms / np.linalg.norm(rms)
        np.testing.assert_allclose(direction, expected, atol=1e-6)
        directions.append(direction)
    np.testing.assert_allclose(*directions, atol=1e-6)
