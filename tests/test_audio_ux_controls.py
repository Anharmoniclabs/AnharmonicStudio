"""Focused regressions for the sampling and low-latency UI controls."""

from __future__ import annotations

import numpy as np
import pytest

from mpclab.audio_kernel import ULTRA_LOW_LATENCY_BLOCKSIZE
from mpclab.engine import Engine
from mpclab.library import Clip
from mpclab.model import Project
from mpclab.ui import main_window, padgrid
from mpclab.ui.main_window import MainWindow
from mpclab.ui.mixer import VUMeter
from PySide6.QtWidgets import QPushButton


class _MemorySettings:
    """Keep profile tests out of the user's real QSettings file."""

    IniFormat = object()
    UserScope = object()
    values: dict[str, object] = {}

    def __init__(self, *_args, **_kwargs):
        pass

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


@pytest.fixture
def window(tmp_path, monkeypatch):
    _MemorySettings.values = {}
    monkeypatch.setattr(main_window, "QSettings", _MemorySettings)
    monkeypatch.setattr(Engine, "start", lambda _engine, device=None: None)
    instance = MainWindow(tmp_path)
    try:
        yield instance
    finally:
        # Profile tests use a sentinel to represent a running stream. Do not
        # let MainWindow.close() try to stop that sentinel.
        instance.engine.stream = None
        instance.close()


def test_normalize_uses_only_the_selected_slice_and_is_undoable(window):
    audio = np.full((1000, 2), 0.95, dtype=np.float32)
    audio[100:500] = 0.25
    window.engine.sr = 1000
    window.library.audio = lambda _sample_id: audio
    pad = window.project.pads[0]
    pad.sample_id = "sample"
    pad.start = 0.1
    pad.end = 0.5

    window.normalize_pad(0)

    target = 10.0 ** (-1.0 / 20.0)
    assert pad.gain == pytest.approx(target / 0.25)
    assert len(window._undo) == 1
    assert window._dirty


def test_tighten_uses_both_channels_and_keeps_edge_padding(window):
    audio = np.zeros((1000, 2), dtype=np.float32)
    audio[300:500, 0] = 1.0
    audio[450:700, 1] = 0.5
    window.engine.sr = 1000
    window.library.audio = lambda _sample_id: audio
    pad = window.project.pads[0]
    pad.sample_id = "sample"
    pad.start = 0.1
    pad.end = 0.9

    window.tighten_pad(0)

    assert pad.start == pytest.approx(0.299)  # 1 ms before first active frame
    assert pad.end == pytest.approx(0.703)  # 3 ms after last active frame
    assert len(window._undo) == 1


def test_pad_inspector_sound_tools_target_the_selected_pad(window):
    pad_index = 19
    window.project.pads[pad_index].sample_id = "sample"
    calls = []
    window.normalize_pad = lambda gi: calls.append(("normalize", gi))
    window.tighten_pad = lambda gi: calls.append(("tighten", gi))
    window.pad_inspector.set_pad(pad_index)

    buttons = {button.text(): button for button in window.pad_inspector.findChildren(QPushButton)}
    buttons["NORMALIZE"].click()
    buttons["TIGHTEN"].click()

    assert calls == [("normalize", pad_index), ("tighten", pad_index)]


def test_browser_exposes_linked_drum_packs_and_folder_filter(window):
    clip = Clip(
        id="pack-kick",
        name="E808_BD-01",
        kind="pack",
        duration=0.3,
        source_path="/packs/Hits/Bass Drum/E808_BD-01.wav",
        pack="MusicRadar 808",
        category="Hits/Bass Drum [BD]",
    )
    window.library.clips[clip.id] = clip

    index = window.browser.source_filter.findData("packs")
    window.browser.source_filter.setCurrentIndex(index)

    assert len(window.browser.list.sounds()) == 1
    item = window.browser.list.sounds()[0]
    assert item.text(0) == "E808_BD-01"
    assert "Bass Drum [BD]" in item.parent().text(0)
    assert "Hits/Bass Drum [BD]" in item.parent().toolTip(0)
    assert window.browser.category_filter.findData("Hits/Bass Drum [BD]") >= 0


def test_context_audition_releases_gate_pad_without_cutting_a_new_hit(window, monkeypatch):
    callbacks = []
    monkeypatch.setattr(
        padgrid.QTimer,
        "singleShot",
        lambda delay, context, callback: callbacks.append((delay, callback)),
    )
    pad = window.project.pads[0]
    pad.sample_id = "sample"
    pad.mode = "gate"
    pressed, released = [], []
    window.pads.padPressed.connect(lambda gi, velocity: pressed.append((gi, velocity)))
    window.pads.padReleased.connect(released.append)

    window.pads._audition_from_menu(0)
    assert pressed == [(0, 1.0)]
    assert callbacks[0][0] == 300
    callbacks[0][1]()
    assert released == [0]

    # A physical press that arrived after the context audition owns the note;
    # the stale timer must not release it.
    window.pads._pressed.add(0)
    window.pads._audition_from_menu(0)
    callbacks[1][1]()
    assert released == [0]


def test_scrubbing_preserves_an_enabled_loop_preview(window):
    calls = []
    window.engine.audition = lambda *args, **kwargs: calls.append((args, kwargs))
    window.current_clip = "sample"
    window.wave.duration = 1.0
    window.wave.set_selection(0.1, 0.8)
    window.btn_loop_range.blockSignals(True)
    window.btn_loop_range.setChecked(True)
    window.btn_loop_range.blockSignals(False)

    window._scrubbed(0.25)

    assert calls == [(("sample", 0.25, 0.8), {"loop": True})]


def test_selecting_chops_reuses_buttons_and_sends_one_preview(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.current_clip = "sample"
    window.wave.duration = 10
    window.wave.markers = [i / 32 for i in range(256)]
    window._rebuild_chips()
    buttons = tuple(window._slice_buttons)
    calls = []
    window.engine.audition = lambda *args, **kwargs: calls.append(args)
    QTest.mousePress(buttons[12], Qt.LeftButton)
    assert len(calls) == 1  # No button-release delay on the audio command.
    QTest.mouseRelease(buttons[12], Qt.LeftButton)
    buttons[13].click()
    assert tuple(window._slice_buttons) == buttons
    assert buttons[13].isChecked()
    assert not buttons[12].isChecked()
    assert calls == [("sample", 12 / 32, 13 / 32), ("sample", 13 / 32, 14 / 32)]


def test_selecting_audio_profile_restarts_and_persists_it(window):
    calls = []
    window.engine.stream = object()

    def restart(frames):
        calls.append(frames)
        window.engine.blocksize = frames

    window.engine.restart = restart
    index = window.audio_buffer.findData(ULTRA_LOW_LATENCY_BLOCKSIZE)
    window.audio_buffer.setCurrentIndex(index)

    assert calls == [ULTRA_LOW_LATENCY_BLOCKSIZE]
    assert _MemorySettings.values["audio/buffer_frames"] == ULTRA_LOW_LATENCY_BLOCKSIZE
    assert window._audio_start_error is None


def test_failed_profile_that_rolls_back_is_not_reported_as_offline(window):
    old_frames = window.engine.blocksize
    window.engine.stream = object()

    def failed_restart(_frames):
        # Engine.restart restores the previous live stream before re-raising.
        window.engine.blocksize = old_frames
        window.engine.stream = object()
        raise RuntimeError("unsupported period")

    window.engine.restart = failed_restart
    index = window.audio_buffer.findData(ULTRA_LOW_LATENCY_BLOCKSIZE)
    window.audio_buffer.setCurrentIndex(index)

    assert window.engine.stream is not None
    assert window.engine.blocksize == old_frames
    assert window.audio_buffer.currentData() == old_frames
    assert window._audio_start_error is None
    assert "restored" in window.status.currentMessage()


def test_audio_menu_lists_outputs_and_persists_selected_connection(window, monkeypatch):
    earpods = {
        "index": 7,
        "kind": "portaudio",
        "name": "EarPods",
        "host": "ALSA",
        "label": "EarPods  ·  ALSA",
        "key": '["ALSA", "EarPods"]',
    }
    speakers = {
        "index": 2,
        "kind": "portaudio",
        "name": "Built-in Speakers",
        "host": "ALSA",
        "label": "Built-in Speakers  ·  ALSA",
        "key": '["ALSA", "Built-in Speakers"]',
    }
    monkeypatch.setattr(
        main_window, "output_device_inventory", lambda: ([speakers, earpods], speakers["index"])
    )
    monkeypatch.setattr(main_window, "pipewire_output_inventory", lambda: [])
    switched = []
    window.engine.stream = object()
    window.engine.restart_device = switched.append

    window._refresh_audio_menu()
    labels = [action.text() for action in window.audio_menu.actions()]
    assert "EarPods  ·  ALSA" in labels
    assert "RECONNECT / FIX AUDIO" in labels

    window._select_audio_output(earpods["key"])

    assert switched == [7]
    assert window._audio_output_key == earpods["key"]
    assert _MemorySettings.values["audio/output_device"] == earpods["key"]
    assert "EarPods" in window.status.currentMessage()


def test_audio_menu_keeps_last_working_route_when_switch_fails(window, monkeypatch):
    earpods = {
        "index": 7,
        "kind": "portaudio",
        "name": "EarPods",
        "host": "ALSA",
        "label": "EarPods  ·  ALSA",
        "key": '["ALSA", "EarPods"]',
    }
    monkeypatch.setattr(main_window, "output_device_inventory", lambda: ([earpods], 7))
    monkeypatch.setattr(main_window, "pipewire_output_inventory", lambda: [])
    window.engine.stream = object()

    def fail(_device):
        raise RuntimeError("device busy")

    window.engine.restart_device = fail
    window._select_audio_output(earpods["key"])

    assert window._audio_output_key == ""
    assert "audio/output_device" not in _MemorySettings.values
    assert window._audio_start_error is None
    assert "device busy" in window.status.currentMessage()


def test_audio_menu_routes_real_pipewire_sink_before_reconnecting(window, monkeypatch):
    speakers = {
        "id": 54,
        "index": 54,
        "kind": "pipewire",
        "name": "Built-in Speakers",
        "host": "PipeWire",
        "label": "Built-in Speakers  ·  PipeWire",
        "key": '["PipeWire", "speaker-node"]',
        "default": True,
    }
    earpods = {
        "id": 77,
        "index": 77,
        "kind": "pipewire",
        "name": "EarPods Analog Stereo",
        "host": "PipeWire",
        "label": "EarPods Analog Stereo  ·  PipeWire",
        "key": '["PipeWire", "earpods-node"]',
        "default": False,
    }
    monkeypatch.setattr(main_window, "output_device_inventory", lambda: ([], None))
    monkeypatch.setattr(main_window, "pipewire_output_inventory", lambda: [speakers, earpods])
    defaults, restarts = [], []
    monkeypatch.setattr(main_window, "set_pipewire_default", defaults.append)
    window.engine.restart_device = restarts.append
    window.engine.stream = object()

    window._select_audio_output(earpods["key"])

    assert defaults == [77]
    assert restarts == [None]
    assert _MemorySettings.values["audio/output_device"] == earpods["key"]
    assert "EarPods Analog Stereo" in window.status.currentMessage()


def test_wpctl_sink_parser_keeps_ids_names_and_default_marker():
    text = """
Audio
 ├─ Sinks:
 │      54. Built-in Speakers                   [vol: 0.45]
 │  *   77. EarPods Analog Stereo               [vol: 1.00]
 │
 ├─ Sources:
 │      55. Microphone                          [vol: 1.00]
"""

    assert main_window._wpctl_sinks(text) == [
        {"id": 54, "name": "Built-in Speakers", "default": False},
        {"id": 77, "name": "EarPods Analog Stereo", "default": True},
    ]


def test_engine_device_reconnect_rolls_back_to_previous_live_output(tmp_path, monkeypatch):
    engine = Engine(type("Library", (), {})())
    engine.output_device = 2
    engine.stream = object()
    starts = []

    def stop():
        engine.stream = None

    def start(device):
        starts.append(device)
        if device == 7:
            raise RuntimeError("unplugged")
        engine.output_device = device
        engine.stream = object()

    monkeypatch.setattr(engine, "stop", stop)
    monkeypatch.setattr(engine, "start", start)

    with pytest.raises(RuntimeError, match="unplugged"):
        engine.restart_device(7)

    assert starts == [7, 2]
    assert engine.output_device == 2
    assert engine.stream is not None


def test_vu_meter_uses_dbfs_scale_and_holds_instantaneous_peaks():
    assert VUMeter._position(1.0) == pytest.approx(1.0)
    assert VUMeter._position(10.0 ** (-30.0 / 20.0)) == pytest.approx(0.5)
    assert VUMeter._position(0.0) == pytest.approx(0.0)

    meter = VUMeter()
    meter.set_level(0.1, 0.8)
    meter.set_level(0.0, 0.0)
    assert meter.level == 0.0
    assert meter.peak == pytest.approx(0.8 * 0.94)


def test_mixer_strip_uses_engine_rms_and_peak_and_marks_edits_dirty(window):
    strip = window.mixer.strips[0]
    window.engine.meters[0] = 0.12
    window.engine.peaks[0] = 0.71
    strip.update_meter()
    assert strip.meter.level == pytest.approx(0.12)
    assert strip.meter.peak == pytest.approx(0.71)

    window._set_dirty(False)
    strip.fader.setValue(73)
    assert window.project.tracks[0].gain == pytest.approx(0.73)
    assert window._dirty


def test_master_mixer_fader_stays_in_sync_with_transport(window):
    window._set_dirty(False)
    window.mixer.master_strip.fader.setValue(91)

    assert window.project.master == pytest.approx(0.91)
    assert window.master_slider.value() == 91
    assert window._dirty


def test_fxrack_changes_request_off_callback_tone_preparation(window):
    prepared = []
    window.engine.prepare_fx = lambda project=None: prepared.append(project)

    window.mixer.rack._touched()

    assert prepared == [window.project]


def test_fxrack_preset_also_requests_tone_preparation(window):
    prepared = []
    window.engine.prepare_fx = lambda project=None: prepared.append(project)
    # Drive the preset handler directly: row 7 is "air", which changes tone.
    window.mixer.rack._apply_preset(7)

    assert window.project.tracks[0].fx.high == pytest.approx(4.0)
    assert prepared == [window.project]


def test_applying_project_prepares_its_tone_kernels(window):
    project = Project()
    project.tracks[2].fx.low = 4.0
    project.master_fx.high = 1.5
    prepared = []
    window.engine.prepare_fx = lambda value=None: prepared.append(value)

    window._apply_project(project)

    assert project in prepared
