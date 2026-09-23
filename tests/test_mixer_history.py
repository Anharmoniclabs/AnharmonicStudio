"""Mixer gestures must undo the audible state, without duplicate automation history."""

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.ui.mixer import QInputDialog
from test_pad_pan import window as window


@pytest.fixture(autouse=True)
def premium_runtime():
    # Runtime hooks must precede Engine construction, as in app startup.
    from mpclab.premium_workflows import install_premium_runtime

    install_premium_runtime()


def control(window, name):
    if name == "master":
        return window.mixer.master_strip.fader
    if name == "transport":
        return window.master_slider
    strip = window.mixer.strips[0]
    return strip.fader if name == "gain" else strip.pan


def value(window, name):
    if name in ("master", "transport"):
        return window.project.master
    return getattr(window.project.tracks[0], name)


@pytest.mark.parametrize("name", ["gain", "pan", "master", "transport"])
def test_slider_gesture_has_one_pre_edit_snapshot(window, name):
    original = value(window, name)
    slider = control(window, name)
    slider.setSliderDown(True)
    for position in (20, 30, 40):
        slider.setValue(position)
    slider.setSliderDown(False)
    assert value(window, name) == 0.4
    assert len(window._undo) == 1
    if name in ("master", "transport"):
        assert window.master_slider.value() == 40
        assert window.mixer.master_strip.fader.value() == 40
    window.undo()
    assert value(window, name) == original
    window.redo()
    assert value(window, name) == 0.4


@pytest.mark.parametrize("name", ["gain", "pan", "master", "transport"])
def test_separate_drags_have_separate_history(window, name):
    original = value(window, name)
    slider = control(window, name)
    for position in (30, 40):
        slider.setSliderDown(True)
        slider.setValue(position)
        slider.setSliderDown(False)
    assert len(window._undo) == 2
    window.undo()
    assert value(window, name) == 0.3
    window.undo()
    assert value(window, name) == original


@pytest.mark.parametrize("name", ["gain", "pan", "master", "transport"])
def test_discrete_keyboard_and_wheel_edits_are_independent(window, name):
    slider = control(window, name)
    original = value(window, name)
    QTest.keyClick(slider, Qt.Key_Down)
    keyboard = value(window, name)
    assert keyboard != original
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(slider, event)
    assert value(window, name) != keyboard
    assert len(window._undo) == 2
    window.undo()
    assert value(window, name) == keyboard
    window.undo()
    assert value(window, name) == original


@pytest.mark.parametrize("name", ["gain", "pan", "master", "transport"])
def test_unchanged_click_and_sync_preserve_precision_and_redo(window, name):
    original = 0.314159265
    if name in ("master", "transport"):
        window.project.master = original
    else:
        setattr(window.project.tracks[0], name, original)
    window.mixer.sync()
    control(window, name).setValue(70)
    window.undo()
    slider = control(window, name)
    slider.setSliderDown(True)
    slider.setSliderDown(False)
    window.mixer.sync()
    assert value(window, name) == original
    assert not window._undo
    assert len(window._redo) == 1


@pytest.mark.parametrize("name", ["mute", "solo"])
def test_buttons_restore_prior_state(window, name):
    button = getattr(window.mixer.strips[0], name)
    button.click()
    assert getattr(window.project.tracks[0], name)
    assert len(window._undo) == 1
    window.undo()
    assert not getattr(window.project.tracks[0], name)
    window.redo()
    assert getattr(window.project.tracks[0], name)


def test_rename_undo_and_unchanged_name(window, monkeypatch):
    original = window.project.tracks[0].name
    strip = window.mixer.strips[0]
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Drums", True))
    strip._rename()
    strip._rename()
    assert len(window._undo) == 1
    window.undo()
    assert window.project.tracks[0].name == original
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: (original, True))
    strip._rename()
    assert not window._undo and len(window._redo) == 1


@pytest.mark.parametrize("mode", ["read", "touch", "latch", "write"])
@pytest.mark.parametrize("name", ["gain", "transport"])
def test_automation_and_base_parameter_share_snapshot(window, mode, name):
    from mpclab.automation_mode_state import install_automation_mode_state
    from mpclab.automation_modes import attach_automation_modes
    from mpclab.premium_workflows import attach_premium_workflows, install_premium_runtime

    install_premium_runtime()
    install_automation_mode_state()
    controller = attach_automation_modes(window, attach_premium_workflows(window))
    controller.timer.stop()
    target = "master" if name == "transport" else "track:0:gain"
    controller.set_mode(target, mode)
    window._undo.clear()
    window._redo.clear()
    original = value(window, name)
    window.engine.mode = "song"
    window.engine.playing = True
    # Do not tick first: Write must capture the old value even when the user
    # moves a control before the automation timer runs after Play.
    slider = control(window, name)
    slider.setSliderDown(True)
    slider.setValue(35)
    slider.setValue(45)
    slider.setSliderDown(False)
    assert len(window._undo) == 1
    assert value(window, name) == 0.45
    window.engine.playing = False
    controller.tick()
    window.undo()
    assert value(window, name) == original
    assert not window.project.automation
    window.redo()
    assert value(window, name) == 0.45
    if mode != "read":
        assert window.project.automation


def test_sync_does_not_write_automation(window):
    from mpclab.automation_mode_state import install_automation_mode_state
    from mpclab.automation_modes import attach_automation_modes
    from mpclab.premium_workflows import attach_premium_workflows, install_premium_runtime

    install_premium_runtime()
    install_automation_mode_state()
    controller = attach_automation_modes(window, attach_premium_workflows(window))
    controller.timer.stop()
    controller.set_mode("track:0:gain", "write")
    window._undo.clear()
    window.project.tracks[0].gain = 0.333333
    window.engine.mode = "song"
    window.engine.playing = True
    window.mixer.sync()
    assert not window._undo
    assert not window.project.automation
    assert window.project.tracks[0].gain == 0.333333
    window.engine.playing = False


def test_gain_undo_restores_actual_audio(window):
    from mpclab.engine import Engine
    from mpclab.model import Project

    def render():
        engine = Engine(window.library, blocksize=512)
        engine.project = Project.from_dict(window.project.to_dict())
        engine.preload_project_audio()
        engine.trigger_pad(0)
        blocks = []
        for _ in range(24):
            block = np.zeros((512, 2), dtype=np.float32)
            engine._callback(block, 512, None, False)
            blocks.append(block)
        return np.concatenate(blocks)

    window.library._audio.clear()
    original_gain = window.project.tracks[0].gain
    original = render()
    window.mixer.strips[0].fader.setValue(50)
    quieter = render()
    np.testing.assert_allclose(quieter[4800:], original[4800:] * (0.5 / original_gain), atol=1e-7)
    window.undo()
    np.testing.assert_array_equal(render(), original)
    window.redo()
    np.testing.assert_array_equal(render(), quieter)
