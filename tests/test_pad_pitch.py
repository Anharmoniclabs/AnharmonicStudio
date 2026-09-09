"""Precise sampler tuning through the real inspector and project history."""

import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QSlider

from mpclab.engine import Engine
from mpclab.export import render_export
from mpclab.model import Project
from mpclab.ui import main_window
from mpclab.ui.padgrid import PadPitchControl
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


def pitch_box(window):
    box = window.pad_inspector.findChild(QDoubleSpinBox, "padPitch")
    assert box is not None, "Pad pitch needs a direct numeric editor"
    return box


def type_pitch(window, text):
    box = pitch_box(window)
    box.selectAll()
    QTest.keyClicks(box, text)
    QTest.keyClick(box, Qt.Key_Return)


def test_pitch_slider_edit_is_one_undo_step(window):
    slider = window.pad_inspector.findChildren(QSlider)[2]
    slider.setSliderDown(True)
    for value in (slider.value() + 10, slider.value() + 20, slider.value() + 30):
        slider.setValue(value)
    slider.setSliderDown(False)
    edited = window.project.pads[19].pitch
    assert edited != 0
    assert len(window._undo) == 1
    window.undo()
    assert window.project.pads[19].pitch == 0
    window.redo()
    assert window.project.pads[19].pitch == edited


def test_cent_entry_commits_once_and_targets_selected_pad(window):
    box = pitch_box(window)
    box.selectAll()
    QTest.keyClicks(box, "7.37")
    assert window.project.pads[19].pitch == 0
    assert not window._undo
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].pitch == 7.37
    assert window.project.pads[0].pitch == 0
    assert len(window._undo) == 1
    assert window._dirty
    window.undo()
    assert pitch_box(window).value() == 0
    window.redo()
    assert pitch_box(window).value() == 7.37


def test_pitch_keyboard_steps_and_limits(window):
    box = pitch_box(window)
    QTest.keyClick(box, Qt.Key_Up)
    assert window.project.pads[19].pitch == 0.01
    QTest.keyClick(box, Qt.Key_Down)
    assert window.project.pads[19].pitch == 0
    slider = window.pad_inspector.findChild(PadPitchControl).slider
    QTest.keyClick(slider, Qt.Key_Right)
    assert window.project.pads[19].pitch == 0.01
    QTest.keyClick(slider, Qt.Key_PageUp)
    assert window.project.pads[19].pitch == 1.01
    type_pitch(window, "24.00")
    history_size = len(window._undo)
    QTest.keyClick(box, Qt.Key_Up)
    assert window.project.pads[19].pitch == 24
    assert len(window._undo) == history_size
    type_pitch(window, "-24.00")
    history_size = len(window._undo)
    QTest.keyClick(box, Qt.Key_Down)
    assert window.project.pads[19].pitch == -24
    assert len(window._undo) == history_size


def test_pitch_escape_cancels_typing_without_stopping_transport(window, monkeypatch):
    type_pitch(window, "-3.27")
    stopped = []
    monkeypatch.setattr(window, "stop_all", lambda: stopped.append(True))
    box = pitch_box(window)
    box.selectAll()
    QTest.keyClicks(box, "12.07")
    QTest.keyClick(box, Qt.Key_Escape)
    assert box.cleanText() == "-3.27"
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].pitch == -3.27
    assert len(window._undo) == 1
    assert not stopped


def test_pitch_focus_out_commits_the_complete_value(window):
    box = pitch_box(window)
    box.selectAll()
    QTest.keyClicks(box, "-0.07")
    QApplication.sendEvent(box, QFocusEvent(QEvent.FocusOut, Qt.TabFocusReason))
    assert window.project.pads[19].pitch == -0.07
    assert len(window._undo) == 1


def test_pitch_rebuild_discards_pending_text_and_preserves_saved_precision(window):
    window.project.pads[19].pitch = 3.14159
    window.pad_inspector.rebuild()
    assert window.project.pads[19].pitch == 3.14159
    assert not window._undo and not window._dirty
    old_box = pitch_box(window)
    old_box.selectAll()
    QTest.keyClicks(old_box, "12.07")
    window.select_pad(0)
    QApplication.sendEvent(old_box, QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
    assert window.project.pads[19].pitch == 3.14159
    assert window.project.pads[0].pitch == 0
    assert not window._undo and not window._dirty
    window.select_pad(1)
    assert window.pad_inspector.findChild(PadPitchControl) is None


def test_pitch_reset_and_no_op_edits_preserve_redo(window):
    type_pitch(window, "7.37")
    control = window.pad_inspector.findChild(PadPitchControl)
    control.reset_action.trigger()
    assert window.project.pads[19].pitch == 0
    assert pitch_box(window).value() == 0
    assert len(window._undo) == 2
    window.undo()
    assert window.project.pads[19].pitch == 7.37
    window.undo()
    control = window.pad_inspector.findChild(PadPitchControl)
    control.reset_action.trigger()
    control.slider.setSliderDown(True)
    control.slider.setSliderDown(False)
    type_pitch(window, "0.00")
    assert not window._undo
    assert len(window._redo) == 2
    window.redo()
    assert window.project.pads[19].pitch == 7.37


def test_pitch_shortcuts_with_numeric_focus(window):
    # This is the test's disposable offscreen widget, never a desktop window.
    assert QApplication.platformName() == "offscreen"
    window.pad_side.show()
    window.show()
    box = pitch_box(window)
    box.setFocus()
    QApplication.processEvents()
    assert QApplication.focusWidget() is box
    type_pitch(window, "7.37")
    QTest.keyClick(box, Qt.Key_0, Qt.AltModifier)
    assert window.project.pads[19].pitch == 0
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier)
    assert window.project.pads[19].pitch == 7.37
    box = pitch_box(window)
    box.setFocus()
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert window.project.pads[19].pitch == 0


def test_pitch_undo_redo_during_typing_stays_in_the_field(window):
    assert QApplication.platformName() == "offscreen"
    window.pad_side.show()
    window.show()
    box = pitch_box(window)
    box.setFocus()
    QApplication.processEvents()
    type_pitch(window, "7.37")
    box.selectAll()
    QTest.keyClicks(box, "12.07")
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier)
    assert window.project.pads[19].pitch == 7.37
    assert len(window._undo) == 1
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert box.cleanText() == "12.07"
    assert window.project.pads[19].pitch == 7.37
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].pitch == 12.07
    assert len(window._undo) == 2


@pytest.mark.parametrize("invalid", ["", "-", "25.00", "nan"])
def test_invalid_pitch_does_not_change_project_or_history(window, invalid):
    box = pitch_box(window)
    box.lineEdit().setText(invalid)
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].pitch == 0
    assert not window._undo
    assert not window._dirty


def test_cent_pitch_and_history_survive_save_and_reopen(window):
    type_pitch(window, "12.07")
    path = window.projects_dir / "cent-tuning.json"
    assert window._save_project_to(path)
    reopened = main_window.MainWindow(window.root, restore_session=False)
    try:
        assert reopened.load_project_path(path)
        reopened.select_pad(19)
        assert pitch_box(reopened).value() == 12.07
        assert reopened.project.pads[19].pitch == 12.07
        assert len(reopened._undo) == 1
        reopened.undo()
        assert reopened.project.pads[19].pitch == 0
        reopened.redo()
        assert reopened.project.pads[19].pitch == 12.07
    finally:
        reopened._dirty = False
        reopened.close()


def frequency(signal, sample_rate=48_000):
    # Interpolate rising zero crossings away from the attack and sample end.
    signal = signal[4_800:19_200, 0].astype(np.float64)
    starts = np.flatnonzero((signal[:-1] <= 0) & (signal[1:] > 0))
    assert len(starts) > 20
    crossings = starts - signal[starts] / (signal[starts + 1] - signal[starts])
    return sample_rate / np.diff(crossings).mean()


@pytest.mark.parametrize("pitch", [-24.0, -0.07, 0.01, 7.37, 24.0])
def test_entered_pitch_is_audible_in_callback_and_export(window, tmp_path, pitch):
    import soundfile as sf

    type_pitch(window, f"{pitch:.2f}")
    # Use a new engine with the saved project representation, no device stream.
    engine = Engine(window.library, blocksize=512)
    engine.project = Project.from_dict(window.project.to_dict())
    engine.preload_project_audio()
    engine.trigger_pad(19)
    chunks = []
    for _ in range(48):
        block = np.zeros((512, 2), dtype=np.float32)
        engine._callback(block, 512, None, False)
        chunks.append(block)
    callback = np.concatenate(chunks)
    engine.project.pattern().bars = 1
    engine.project.pattern().steps = {19: {0: 1.0}}
    destination = tmp_path / "tuned.wav"
    render_export(
        engine.project, window.library, destination, mode="pattern", tail=0, subtype="FLOAT"
    )
    exported, sr = sf.read(destination, dtype="float32", always_2d=True)
    expected = 440 * 2 ** (pitch / 12)
    for signal in (callback, exported):
        assert np.isfinite(signal).all()
        cents_error = 1200 * np.log2(frequency(signal, sr) / expected)
        assert abs(cents_error) < 0.02
    # Callback and offline resamplers have different quality settings, but
    # their tuning must agree to well below the editor's one-cent resolution.
    assert abs(1200 * np.log2(frequency(callback) / frequency(exported))) < 0.02
