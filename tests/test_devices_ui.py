"""Controller performances and asynchronous plugin changes, without hardware."""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from mpclab.engine import Engine
from mpclab.midi_devices import MidiPort
from mpclab.ui import devices, main_window
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    app = main_window.MainWindow(tmp_path, restore_session=False)
    app._ui_timer.stop()
    app._autosave_timer.stop()
    app.project.vocal_record.count_in_bars = 0
    yield app
    app._dirty = False
    app.close()


def until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Device operation did not complete")


def test_midi_records_song_notes_and_unplug_finishes_them(window):
    controller = window.devices
    row = window.project.rows[2]
    row.record_source = "notes"
    window.track_inspector.select_row(row)
    window.track_inspector.arm.click()
    window.btn_rec.click()
    window.engine._process_commands()
    window.engine.beat = 0.125
    controller.service._emit("keyboard", [0x90, 60, 80])
    controller._tick()
    window.engine.beat = 0.625
    controller.service._emit("keyboard", [])
    controller._tick()
    window.stop_all()
    pattern = next(p for p in window.project.patterns if p.id == row.clips[0].ref)
    assert [(n.pitch, n.start, n.duration) for n in pattern.notes] == [(60, 0.125, 0.5)]
    assert pattern.notes[0].velocity == pytest.approx(80 / 127)


def test_dialog_learn_release_button_and_remembered_settings(window):
    controller = window.devices
    controller.service.ports = (MidiPort("mpk", "Keyboard", 0, True),)
    dialog = devices.DevicesDialog(controller, window)
    dialog.learn_target.setCurrentIndex(dialog.learn_target.findData("pad:5"))
    dialog._learn()
    controller.router.handle("mpk", [0x90, 72, 110])
    controller._tick()
    assert '"72": 5' in window.settings.value("midi/controllers")
    controller.router.handle("mpk", [0x90, 72, 110])
    assert controller.router.held
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Release held notes").click()
    assert not controller.router.held
    dialog.mode.setCurrentText("Pads")
    dialog.base_note.setValue(48)
    dialog.enabled.setChecked(False)
    assert window.settings.value("midi/disabled") == '["mpk"]'
    restored = devices.DevicesController(window)
    try:
        assert restored.router.settings["mpk"]["pad_base"] == 48
        assert restored.router.settings["mpk"]["pads"] == {"72": 5}
        assert restored.service.disabled == {"mpk"}
    finally:
        restored.shutdown()


def test_plugin_loads_are_bounded_and_removed_pending_plugins_stay_removed(window, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    loaded = []

    class Plugin:
        def __init__(self, specification, rate):
            loaded.append(specification["path"])
            entered.set()
            assert release.wait(3)
            self.info = {"name": "Fixture", "instrument": True, "effect": True, "state": ""}
            self.closed = False

        def render(self, *args, **kwargs):
            return np.zeros((128, 2), np.float32)

        def close(self):
            self.closed = True

    monkeypatch.setattr(devices, "IsolatedPlugin", Plugin)
    monkeypatch.setattr(
        devices,
        "LivePlugin",
        lambda p, n: SimpleNamespace(info=p.info, blocksize=n, error="", close=p.close),
    )
    controller = window.devices
    controller.load_plugin("instrument", {"path": "first.vst3"})
    assert entered.wait(1)
    controller.load_plugin("instrument", {"path": "second.vst3"})
    controller.load_plugin("instrument", {"path": "last.vst3"})
    controller.load_plugin("effect", {"path": "effect.vst3"})
    controller.remove_plugin("instrument")
    release.set()
    until(lambda: not controller._loading and not controller._pending_loads)
    assert loaded == ["first.vst3", "effect.vst3"]
    assert window.engine.external.instrument is None
    assert set(window.project.plugins) == {"effect"}


def test_missing_project_instrument_does_not_prevent_loading_its_effect(window, monkeypatch):
    class Plugin:
        def __init__(self, specification, rate):
            if "missing" in specification["path"]:
                raise ValueError("Missing instrument")
            self.info = {"name": "Effect", "effect": True, "state": ""}

        def render(self, *args, **kwargs):
            return np.zeros((128, 2), np.float32)

        def close(self):
            pass

    monkeypatch.setattr(devices, "IsolatedPlugin", Plugin)
    monkeypatch.setattr(
        devices,
        "LivePlugin",
        lambda p, n: SimpleNamespace(info=p.info, blocksize=n, error="", close=p.close),
    )
    window.project.plugins = {
        "instrument": {"path": "missing.vst3"},
        "effect": {"path": "effect.vst3"},
    }
    window.devices.sync_project()
    until(lambda: not window.devices._loading and not window.devices._pending_loads)
    assert window.engine.external.effect is not None
    assert window.engine.external.instrument.error == "Missing instrument"
    assert "instrument" in window.project.plugins
