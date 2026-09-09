"""Millisecond entry must preserve articulation, history, and rendered sound."""

import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDoubleSpinBox

from mpclab.ui import main_window
from test_pad_gain import finish_offscreen_events as finish_offscreen_events
from test_pad_pan import window as window


PARAMETERS = [
    ("attack", "padAttack", 2.0, 0.5, 400.0),
    ("release", "padRelease", 30.0, 5.0, 1500.0),
    ("loop_crossfade", "padLoopCrossfade", 5.0, 0.0, 50.0),
]


@pytest.fixture(params=PARAMETERS, ids=lambda p: p[0])
def parameter(request):
    return request.param


def box_for(window, parameter):
    box = window.pad_inspector.findChild(QDoubleSpinBox, parameter[1])
    assert box is not None, f"{parameter[0]} needs direct millisecond entry"
    return box


def enter(window, parameter, text):
    box = box_for(window, parameter)
    box.selectAll()
    QTest.keyClicks(box, text)
    QTest.keyClick(box, Qt.Key_Return)


def test_entry_deferred_selected_and_undoable(window, parameter):
    attr, _, default, _, _ = parameter
    box = box_for(window, parameter)
    box.selectAll()
    QTest.keyClicks(box, "12.7")
    assert getattr(window.project.pads[19], attr) == default / 1000
    assert not window._undo
    QTest.keyClick(box, Qt.Key_Return)
    assert getattr(window.project.pads[19], attr) == 0.0127
    assert getattr(window.project.pads[0], attr) == default / 1000
    assert len(window._undo) == 1 and window._dirty
    window.undo()
    assert getattr(window.project.pads[19], attr) == default / 1000
    window.redo()
    assert box_for(window, parameter).value() == 12.7


def test_fine_steps_focus_commit_and_limits(window, parameter):
    attr, _, default, lo, hi = parameter
    box = box_for(window, parameter)
    assert box.suffix() == " ms"
    QTest.keyClick(box, Qt.Key_Up)
    assert getattr(window.project.pads[19], attr) == pytest.approx((default + 0.1) / 1000)
    box.selectAll()
    QTest.keyClicks(box, "13.2")
    QApplication.sendEvent(box, QFocusEvent(QEvent.FocusOut, Qt.TabFocusReason))
    assert getattr(window.project.pads[19], attr) == 0.0132
    for value, key in ((lo, Qt.Key_Down), (hi, Qt.Key_Up)):
        enter(window, parameter, str(value))
        count = len(window._undo)
        QTest.keyClick(box, key)
        assert getattr(window.project.pads[19], attr) == value / 1000
        assert len(window._undo) == count


def test_reset_escape_and_noop_redo(window, parameter, monkeypatch):
    attr, _, default, _, _ = parameter
    stopped = []
    monkeypatch.setattr(window, "stop_all", lambda: stopped.append(True))
    box = box_for(window, parameter)
    box.selectAll()
    QTest.keyClicks(box, "12.7")
    QTest.keyClick(box, Qt.Key_Escape)
    QTest.keyClick(box, Qt.Key_Return)
    assert not window._undo and not stopped
    enter(window, parameter, "12.7")
    box.parent().reset_action.trigger()
    assert getattr(window.project.pads[19], attr) == default / 1000
    window.undo()
    window.undo()
    box_for(window, parameter).parent().reset_action.trigger()
    enter(window, parameter, str(default))
    assert not window._undo and len(window._redo) == 2


@pytest.mark.parametrize("invalid", ["", "-", "nan", "-1", "1501"])
def test_invalid_entry_preserves_sound(window, parameter, invalid):
    box = box_for(window, parameter)
    box.lineEdit().setText(invalid)
    QTest.keyClick(box, Qt.Key_Return)
    assert getattr(window.project.pads[19], parameter[0]) == parameter[2] / 1000
    assert not window._undo and not window._dirty


@pytest.mark.parametrize("original", [0.00012345, 0.0123456789, 2.123456789])
def test_saved_precision_and_retired_controls(window, parameter, original):
    attr = parameter[0]
    setattr(window.project.pads[19], attr, original)
    window.pad_inspector.rebuild()
    box = box_for(window, parameter)
    if original * 1000 < parameter[3]:
        assert box.text().startswith("< ")
    elif original * 1000 > parameter[4]:
        assert box.text().startswith("> ")
    enter(window, parameter, box.cleanText())
    assert getattr(window.project.pads[19], attr) == original
    assert not window._undo and not window._dirty
    box.selectAll()
    QTest.keyClicks(box, "24.3")
    old_pad = window.project.pads[19]
    window.select_pad(0)
    QApplication.sendEvent(box, QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
    box.parent().reset_action.trigger()
    box.parent().slider.setValue(400)
    assert getattr(old_pad, attr) == original
    assert not window._undo and not window._dirty
    window.select_pad(1)
    assert window.pad_inspector.findChild(QDoubleSpinBox, parameter[1]) is None


def test_numeric_keyboard_history_ownership(window, parameter):
    assert QApplication.platformName() == "offscreen"
    window.pad_side.show()
    window.show()
    box = box_for(window, parameter)
    box.setFocus()
    QApplication.processEvents()
    enter(window, parameter, "12.7")
    box.selectAll()
    QTest.keyClicks(box, "24.3")
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier)
    assert len(window._undo) == 1
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert box.cleanText() == "24.3"
    QTest.keyClick(box, Qt.Key_Escape)
    QTest.keyClick(box, Qt.Key_0, Qt.AltModifier)
    assert getattr(window.project.pads[19], parameter[0]) == parameter[2] / 1000
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier)
    assert getattr(window.project.pads[19], parameter[0]) == 0.0127
    box = box_for(window, parameter)
    box.setFocus()
    QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert getattr(window.project.pads[19], parameter[0]) == parameter[2] / 1000


def test_articulation_history_survives_reopen(window):
    for parameter in PARAMETERS:
        enter(window, parameter, "12.7")
    enter(window, PARAMETERS[2], "0")
    path = window.projects_dir / "articulation.json"
    assert window._save_project_to(path)
    reopened = main_window.MainWindow(window.root, restore_session=False)
    try:
        assert reopened.load_project_path(path)
        assert reopened.project.pads[19].loop_crossfade == 0
        reopened.undo()
        assert reopened.project.pads[19].loop_crossfade == 0.0127
        for _ in PARAMETERS:
            reopened.undo()
        for attr, _, default, _, _ in PARAMETERS:
            assert getattr(reopened.project.pads[19], attr) == default / 1000
        for _ in range(4):
            reopened.redo()
        assert reopened.project.pads[19].attack == 0.0127
        assert reopened.project.pads[19].release == 0.0127
        assert reopened.project.pads[19].loop_crossfade == 0
    finally:
        reopened._dirty = False
        reopened.close()


@pytest.mark.parametrize(
    "index,milliseconds",
    [
        (0, 0.5),
        (0, 12.7),
        (0, 400),
        (1, 5),
        (1, 127.3),
        (1, 1500),
        (2, 0),
        (2, 0.1),
        (2, 12.7),
        (2, 50),
    ],
)
def test_entered_times_render_in_callback_export_and_undo(window, tmp_path, index, milliseconds):
    import soundfile as sf

    from mpclab.engine import Engine
    from mpclab.export import render_export
    from mpclab.model import Project

    parameter = PARAMETERS[index]
    # Flat source exposes the envelope exactly; a ramp exposes loop-wrap jumps.
    source = (
        np.linspace(-0.1, 0.1, 3840, dtype=np.float32)
        if index == 2
        else np.full(28800, 0.1, dtype=np.float32)
    )
    clip = window.library.add_audio(np.column_stack((source, source)), "Synthetic articulation")
    pad = window.project.pads[19]
    pad.sample_id, pad.end = clip.id, clip.duration
    pad.mode = "loop" if index == 2 else "one-shot"
    window.library._audio.clear()
    window.pad_inspector.rebuild()

    def render():
        engine = Engine(window.library, blocksize=512)
        engine.project = Project.from_dict(window.project.to_dict())
        engine.preload_project_audio()
        engine.trigger_pad(19)
        blocks = []
        for _ in range(72):
            block = np.zeros((512, 2), dtype=np.float32)
            engine._callback(block, 512, None, False)
            blocks.append(block)
        callback = np.concatenate(blocks)
        engine.project.pattern().bars = 1
        engine.project.pattern().steps = {19: {0: 1.0}}
        path = tmp_path / "articulation.wav"
        render_export(engine.project, window.library, path, mode="pattern", tail=0, subtype="FLOAT")
        exported, sr = sf.read(path, dtype="float32", always_2d=True)
        assert sr == 48000
        assert np.isfinite(callback).all() and np.isfinite(exported).all()
        np.testing.assert_allclose(callback, exported[: len(callback)], atol=1e-7, rtol=0)
        return callback, exported

    original = render()
    enter(window, parameter, str(milliseconds))
    edited = render()
    assert np.max(np.abs(edited[0] - original[0])) > 1e-4
    if index < 2:
        pad = window.project.pads[19]
        age = np.arange(len(edited[0]))
        envelope = np.minimum(
            np.clip(age / int(pad.attack * 48000), 0, 1),
            np.clip((len(source) - age) / int(pad.release * 48000), 0, 1),
        )
        expected = envelope[:, None] * original[0][4800]
        np.testing.assert_allclose(edited[0], expected, atol=1e-7, rtol=0)
        assert not np.any(edited[0][len(source) :])
    else:
        jump_before = np.max(np.abs(np.diff(original[0][1000:], axis=0)))
        jump_after = np.max(np.abs(np.diff(edited[0][1000:], axis=0)))
        if milliseconds == 0:
            assert jump_after > jump_before * 10
        else:
            # Even 0.1 ms blends several source frames; 50 ms exercises the
            # short-slice cap below half this 80 ms source.
            dry_jump = np.ptp(original[0][1000:3000, 0]) * (3840 / 2000)
            assert jump_after < dry_jump * 0.6
    window.undo()
    for actual, expected in zip(render(), original, strict=True):
        np.testing.assert_array_equal(actual, expected)
    window.redo()
    for actual, expected in zip(render(), edited, strict=True):
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("mode", ["gate", "loop"])
@pytest.mark.parametrize("milliseconds", [5, 127.3, 1500])
def test_entered_release_shapes_note_off(window, mode, milliseconds):
    from mpclab.engine import Engine
    from mpclab.model import Project

    clip = window.library.add_audio(np.full((96000, 2), 0.1, dtype=np.float32), "Held flat signal")
    pad = window.project.pads[19]
    pad.sample_id, pad.end, pad.mode = clip.id, 2, mode
    window.pad_inspector.rebuild()
    enter(window, PARAMETERS[1], str(milliseconds))
    engine = Engine(window.library, blocksize=512)
    engine.project = Project.from_dict(window.project.to_dict())
    engine.preload_project_audio()
    engine.trigger_pad(19)
    block = np.zeros((512, 2), dtype=np.float32)
    for _ in range(10):
        engine._callback(block, 512, None, False)
    level = block[-1].copy()
    assert np.min(level) > 0.01
    engine.release_pad(19)
    chunks = []
    for _ in range(150):
        engine._callback(block, 512, None, False)
        chunks.append(block.copy())
    output = np.concatenate(chunks)
    release_frames = int(milliseconds / 1000 * 48000)
    expected = np.clip(1 - np.arange(len(output)) / release_frames, 0, 1)[:, None] * level
    np.testing.assert_allclose(output, expected, atol=1e-7, rtol=0)
    assert not np.any(output[release_frames:])
