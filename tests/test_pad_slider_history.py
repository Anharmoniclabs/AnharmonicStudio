"""Continuous sound shaping must preserve the sound before each gesture."""

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QSlider

from mpclab.ui import main_window
from test_pad_pan import window as window  # shared synthetic, device-free fixture


PARAMETERS = [("gain", 0), ("attack", 3), ("release", 4), ("loop_crossfade", 5)]


def slider_for(window, index):
    return window.pad_inspector.findChildren(QSlider)[index]


@pytest.mark.parametrize("parameter,index", PARAMETERS)
def test_drag_restores_exact_original_and_redoes_final_value(window, parameter, index):
    original = getattr(window.project.pads[19], parameter)
    slider = slider_for(window, index)
    slider.setSliderDown(True)
    for position in (300, 350, 400):
        slider.setValue(position)
    slider.setSliderDown(False)
    edited = getattr(window.project.pads[19], parameter)
    assert edited != original
    assert len(window._undo) == 1
    assert getattr(window.project.pads[0], parameter) == original
    window.undo()
    assert getattr(window.project.pads[19], parameter) == original
    window.redo()
    assert getattr(window.project.pads[19], parameter) == edited


@pytest.mark.parametrize("parameter,index", PARAMETERS)
def test_separate_gestures_and_keyboard_steps_have_separate_history(window, parameter, index):
    original = getattr(window.project.pads[19], parameter)
    for position in (300, 400):
        slider = slider_for(window, index)
        slider.setSliderDown(True)
        slider.setValue(position)
        slider.setSliderDown(False)
    middle = getattr(window.project.pads[19], parameter)
    QTest.keyClick(slider, Qt.Key_Right)
    assert len(window._undo) == 3
    window.undo()
    assert getattr(window.project.pads[19], parameter) == middle
    window.undo()
    window.undo()
    assert getattr(window.project.pads[19], parameter) == original


@pytest.mark.parametrize("parameter,index", PARAMETERS)
def test_click_without_movement_and_rebuild_preserve_precision_and_redo(window, parameter, index):
    original = 0.0123456789
    setattr(window.project.pads[19], parameter, original)
    window.pad_inspector.rebuild()
    slider = slider_for(window, index)
    slider.setValue(400)
    window.undo()
    slider = slider_for(window, index)
    slider.setSliderDown(True)
    slider.setValue(slider.value())
    slider.setSliderDown(False)
    assert getattr(window.project.pads[19], parameter) == original
    assert not window._undo
    assert len(window._redo) == 1
    window.redo()
    assert getattr(window.project.pads[19], parameter) != original


@pytest.mark.parametrize("parameter,index", PARAMETERS)
def test_retired_slider_cannot_mutate_or_record_history(window, parameter, index):
    old_pad = window.project.pads[19]
    original = getattr(old_pad, parameter)
    slider = slider_for(window, index)
    slider.setSliderDown(True)
    window.select_pad(0)
    slider.setValue(400)
    slider.setSliderDown(False)
    assert getattr(old_pad, parameter) == original
    assert not window._undo and not window._dirty


def test_shaping_and_history_survive_save_reopen(window):
    original = {p: getattr(window.project.pads[19], p) for p, _ in PARAMETERS}
    for _, index in PARAMETERS:
        slider_for(window, index).setValue(400)
    edited = {p: getattr(window.project.pads[19], p) for p, _ in PARAMETERS}
    path = window.projects_dir / "shaping.json"
    assert window._save_project_to(path)
    reopened = main_window.MainWindow(window.root, restore_session=False)
    try:
        assert reopened.load_project_path(path)
        for _ in PARAMETERS:
            reopened.undo()
        assert {p: getattr(reopened.project.pads[19], p) for p, _ in PARAMETERS} == original
        for _ in PARAMETERS:
            reopened.redo()
        assert {p: getattr(reopened.project.pads[19], p) for p, _ in PARAMETERS} == edited
    finally:
        reopened._dirty = False
        reopened.close()


def test_gain_undo_restores_audible_level(window):
    from mpclab.engine import Engine
    from mpclab.model import Project

    def render():
        engine = Engine(window.library, blocksize=512)
        engine.project = Project.from_dict(window.project.to_dict())
        engine.preload_project_audio()
        engine.trigger_pad(19)
        blocks = []
        for _ in range(24):
            block = np.zeros((512, 2), dtype=np.float32)
            engine._callback(block, 512, None, False)
            blocks.append(block)
        return np.concatenate(blocks)

    # Read the persisted PCM24 fixture before comparing history restores,
    # which reload library audio from disk rather than the creation cache.
    window.library._audio.clear()
    original = render()
    slider_for(window, 0).setValue(125)  # 0.5 linear gain
    quieter = render()
    np.testing.assert_allclose(quieter[4800:], original[4800:] * 0.5, atol=1e-7)
    window.undo()
    np.testing.assert_array_equal(render(), original)
    window.redo()
    np.testing.assert_array_equal(render(), quieter)
