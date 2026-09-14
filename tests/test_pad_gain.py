"""Decibel entry must change the selected sound safely, including exact silence."""

import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox

from mpclab.ui import main_window
from test_pad_pan import window as window  # synthetic audio, isolated settings, no devices


@pytest.fixture(autouse=True)
def finish_offscreen_events():
    # Run after the window fixture closes, so its queued focus/deletion events
    # cannot steal focus from the next test's disposable offscreen window.
    yield
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()


def gain_box(window):
    box = window.pad_inspector.findChild(QDoubleSpinBox, "padGain")
    assert box is not None, "Pad gain needs direct dB entry"
    return box


def control(window):
    return gain_box(window).parent()


def type_gain(window, text):
    box = gain_box(window)
    box.selectAll()
    QTest.keyClicks(box, text)
    QTest.keyClick(box, Qt.Key_Return)


def test_gain_entry_is_deferred_selected_and_undoable(window):
    box = gain_box(window)
    box.selectAll()
    QTest.keyClicks(box, "-6.25")
    assert window.project.pads[19].gain == 1
    assert not window._undo
    QTest.keyClick(box, Qt.Key_Return)
    expected = 10 ** (-6.25 / 20)
    assert window.project.pads[19].gain == pytest.approx(expected)
    assert window.project.pads[0].gain == 1
    assert len(window._undo) == 1 and window._dirty
    window.undo()
    assert window.project.pads[19].gain == 1
    window.redo()
    assert window.project.pads[19].gain == expected


def test_gain_reset_mute_and_limits(window):
    c = control(window)
    c.mute_action.trigger()
    assert window.project.pads[19].gain == 0
    assert gain_box(window).text() == "-∞ dB"
    QTest.keyClick(gain_box(window), Qt.Key_Up)
    assert window.project.pads[19].gain == pytest.approx(1e-6)
    c.reset_action.trigger()
    assert window.project.pads[19].gain == 1
    assert gain_box(window).value() == 0
    c.slider.setValue(c.slider.maximum())
    assert window.project.pads[19].gain == 4
    assert gain_box(window).value() == 12.04
    count = len(window._undo)
    QTest.keyClick(gain_box(window), Qt.Key_Up)
    assert window.project.pads[19].gain == 4
    assert len(window._undo) == count
    c.slider.setValue(c.slider.minimum())
    assert window.project.pads[19].gain == 0
    count = len(window._undo)
    QTest.keyClick(gain_box(window), Qt.Key_Down)
    assert window.project.pads[19].gain == 0
    assert len(window._undo) == count


def test_gain_fine_keys_and_focus_commit(window):
    box = gain_box(window)
    QTest.keyClick(box, Qt.Key_Down)
    assert window.project.pads[19].gain == pytest.approx(10 ** (-0.1 / 20))
    box.selectAll()
    QTest.keyClicks(box, "3.25")
    QApplication.sendEvent(box, QFocusEvent(QEvent.FocusOut, Qt.TabFocusReason))
    assert window.project.pads[19].gain == pytest.approx(10 ** (3.25 / 20))
    assert len(window._undo) == 2


def test_gain_step_reaches_floor_before_exact_mute(window):
    type_gain(window, "-119.95")
    QTest.keyClick(gain_box(window), Qt.Key_Down)
    assert window.project.pads[19].gain == pytest.approx(1e-6)
    assert gain_box(window).text() == "-120.00 dB"
    assert len(window._undo) == 2
    QTest.keyClick(gain_box(window), Qt.Key_Down)
    assert window.project.pads[19].gain == 0
    assert len(window._undo) == 3
    window.undo()
    assert window.project.pads[19].gain == pytest.approx(1e-6)


def test_gain_entry_between_floor_and_mute_snaps_to_floor(window):
    type_gain(window, "-120.05")
    assert window.project.pads[19].gain == pytest.approx(1e-6)
    assert gain_box(window).text() == "-120.00 dB"
    assert len(window._undo) == 1


def test_gain_escape_cancels_without_transport_stop(window, monkeypatch):
    stopped = []
    monkeypatch.setattr(window, "stop_all", lambda: stopped.append(True))
    box = gain_box(window)
    box.selectAll()
    QTest.keyClicks(box, "-9.25")
    QTest.keyClick(box, Qt.Key_Escape)
    QTest.keyClick(box, Qt.Key_Return)
    assert window.project.pads[19].gain == 1
    assert not window._undo and not stopped


@pytest.mark.parametrize("value", [0, 1, 4, 0.314159265, 1e-9])
def test_gain_rebuild_and_noop_preserve_exact_saved_value(window, value):
    window.project.pads[19].gain = value
    window.pad_inspector.rebuild()
    box = gain_box(window)
    if 0 < value < 1e-6:
        assert box.text().startswith("< -120.00")
    QTest.keyClick(box, Qt.Key_Return)
    QApplication.sendEvent(box, QFocusEvent(QEvent.FocusOut, Qt.TabFocusReason))
    c = control(window)
    c.slider.setSliderDown(True)
    c.slider.setSliderDown(False)
    assert window.project.pads[19].gain == value
    assert not window._undo and not window._dirty


def test_gain_retired_control_and_empty_state(window):
    c = control(window)
    box = gain_box(window)
    box.selectAll()
    QTest.keyClicks(box, "-6.25")
    old_pad = window.project.pads[19]
    window.select_pad(0)
    QApplication.sendEvent(box, QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
    c.mute_action.trigger()
    c.reset_action.trigger()
    c.slider.setValue(125)
    assert old_pad.gain == 1 and window.project.pads[0].gain == 1
    assert not window._undo and not window._dirty
    window.select_pad(1)
    assert window.pad_inspector.findChild(QDoubleSpinBox, "padGain") is None


@pytest.mark.parametrize("invalid", ["", "-", "13", "nan", "-121"])
def test_invalid_gain_preserves_project(window, invalid):
    gain_box(window).lineEdit().setText(invalid)
    QTest.keyClick(gain_box(window), Qt.Key_Return)
    assert window.project.pads[19].gain == 1
    assert not window._undo and not window._dirty


def test_gain_noops_preserve_redo(window):
    type_gain(window, "-6.25")
    window.undo()
    control(window).reset_action.trigger()
    type_gain(window, "0")
    assert not window._undo and len(window._redo) == 1
    window.redo()
    assert window.project.pads[19].gain == pytest.approx(10 ** (-6.25 / 20))


def test_gain_keyboard_reset_and_history_ownership(window):
    assert QApplication.platformName() == "offscreen"
    window.pad_side.show()
    window.show()
    box = gain_box(window)
    box.setFocus()
    QApplication.processEvents()
    type_gain(window, "-6.25")
    box.selectAll()
    QTest.keyClicks(box, "-9.25")
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier)
    assert len(window._undo) == 1
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert box.cleanText() == "-9.25"
    QTest.keyClick(box, Qt.Key_Escape)
    QTest.keyClick(box, Qt.Key_0, Qt.AltModifier)
    assert window.project.pads[19].gain == 1
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier)
    assert window.project.pads[19].gain == pytest.approx(10 ** (-6.25 / 20))
    box = gain_box(window)
    box.setFocus()
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert window.project.pads[19].gain == 1


def test_gain_and_mute_history_survive_reopen(window):
    type_gain(window, "-6.25")
    expected = window.project.pads[19].gain
    control(window).mute_action.trigger()
    path = window.projects_dir / "gain.json"
    assert window._save_project_to(path)
    reopened = main_window.MainWindow(window.root, restore_session=False)
    try:
        assert reopened.load_project_path(path)
        reopened.select_pad(19)
        assert reopened.project.pads[19].gain == 0
        assert gain_box(reopened).text() == "-∞ dB"
        reopened.undo()
        assert reopened.project.pads[19].gain == expected
        reopened.undo()
        assert reopened.project.pads[19].gain == 1
        reopened.redo()
        assert reopened.project.pads[19].gain == expected
        reopened.redo()
        assert reopened.project.pads[19].gain == 0
    finally:
        reopened._dirty = False
        reopened.close()


@pytest.mark.parametrize("db", [-120, -6.25, 0, 12.04, None])
def test_entered_gain_matches_callback_export_and_history(window, tmp_path, db):
    import soundfile as sf

    from mpclab.engine import Engine
    from mpclab.export import render_export
    from mpclab.model import Project

    window.library._audio.clear()  # compare the persisted PCM24 fixture throughout

    def render():
        engine = Engine(window.library, blocksize=512)
        engine.project = Project.from_dict(window.project.to_dict())
        engine.preload_project_audio()
        engine.trigger_pad(19)
        blocks = []
        for _ in range(48):
            block = np.zeros((512, 2), dtype=np.float32)
            engine._callback(block, 512, None, False)
            blocks.append(block)
        engine.project.pattern().bars = 1
        engine.project.pattern().steps = {19: {0: 1.0}}
        path = tmp_path / "gain.wav"
        render_export(engine.project, window.library, path, mode="pattern", tail=0, subtype="FLOAT")
        exported, sr = sf.read(path, dtype="float32", always_2d=True)
        assert sr == 48_000
        return np.concatenate(blocks), exported

    original = render()
    if db is None:
        control(window).mute_action.trigger()
        expected = 0
    else:
        type_gain(window, str(db))
        expected = 4 if db == 12.04 else 10 ** (db / 20)
    edited = render()
    for signal, baseline in zip(edited, original, strict=True):
        assert np.isfinite(signal).all()
        if expected == 0:
            assert not np.any(signal)
        else:
            np.testing.assert_allclose(
                signal[4800:19200],
                baseline[4800:19200] * expected,
                rtol=2e-6,
                atol=1e-7 * expected,
            )
    if db != 0:
        window.undo()
        for signal, baseline in zip(render(), original, strict=True):
            np.testing.assert_array_equal(signal, baseline)
        window.redo()
        for signal, baseline in zip(render(), edited, strict=True):
            np.testing.assert_array_equal(signal, baseline)
