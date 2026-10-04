"""Audio changes preserve active takes, previous routes, and note ownership."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QComboBox

from mpclab.engine import Engine
from mpclab.recording import MidiCaptureSession, SamplerCaptureSession
from mpclab.ui import main_window
from mpclab.ui.main_window import MainWindow


class Settings:
    def __init__(self):
        self.values = {}

    def setValue(self, key, value):
        self.values[key] = value


class Status:
    def __init__(self):
        self.message = ""

    def showMessage(self, message, *_args):
        self.message = message

    def currentMessage(self):
        return self.message


def window_stub():
    return SimpleNamespace(
        track_capture=SimpleNamespace(busy=False),
        vocal_panel=SimpleNamespace(recorder=SimpleNamespace(recording=False)),
        engine=SimpleNamespace(
            blocksize=512, output_channels=(0, 1), output_device=2, stream=object()
        ),
        status=Status(),
        settings=Settings(),
        _audio_output_key="old",
        _audio_start_error=None,
        _refresh_audio_menu=lambda: None,
    )


def mark_take(window, owner):
    window.track_capture.busy = owner == "track"
    window.vocal_panel.recorder.recording = owner == "vocal"
    window.vocal_panel._counting = owner == "vocal_count"
    window.engine.recording = owner == "pattern"
    window._record_count_deadline = 123.0 if owner == "count_in" else None


@pytest.mark.parametrize("owner", ["track", "vocal", "pattern", "count_in", "vocal_count"])
def test_output_switch_cannot_interrupt_a_take(owner, monkeypatch):
    window = window_stub()
    mark_take(window, owner)
    monkeypatch.setattr(
        main_window, "output_device_inventory", lambda: pytest.fail("scanned during take")
    )
    assert MainWindow._select_audio_output(window, "new") is False
    assert window._audio_output_key == "old"
    assert not window.settings.values
    assert "Finish the take" in window.status.message


@pytest.mark.parametrize("owner", ["track", "vocal", "pattern", "count_in", "vocal_count"])
def test_buffer_switch_cannot_interrupt_a_take_and_restores_control(owner):
    window = window_stub()
    mark_take(window, owner)
    window.audio_buffer = QComboBox()
    window.audio_buffer.addItem("512", 512)
    window.audio_buffer.addItem("256", 256)
    window.audio_buffer.setCurrentIndex(1)
    MainWindow._audio_buffer_changed(window, 1)
    assert window.audio_buffer.currentData() == 512
    assert window.engine.blocksize == 512
    assert not window.settings.values


@pytest.mark.parametrize("owner", ["track", "vocal", "pattern", "count_in", "vocal_count"])
def test_reconnect_and_calibration_cannot_reset_an_active_take(owner, monkeypatch):
    window = window_stub()
    mark_take(window, owner)
    monkeypatch.setattr(
        main_window.window_transport,
        "_retry_audio",
        lambda *_args: pytest.fail("reset take timing"),
    )
    MainWindow._retry_audio(window)
    assert "Finish the take" in window.status.message
    with pytest.raises(RuntimeError, match="Finish the current take"):
        MainWindow._run_setup_loopback_calibration(window)


def test_pipewire_switch_failure_restores_previous_direct_device(monkeypatch):
    window = window_stub()
    sinks = [
        {"id": 10, "key": "old-system", "default": True, "kind": "pipewire"},
        {"id": 20, "key": "new", "default": False, "kind": "pipewire"},
    ]
    monkeypatch.setattr(main_window, "output_device_inventory", lambda: ([], None))
    monkeypatch.setattr(main_window, "pipewire_output_inventory", lambda: sinks)
    defaults, starts = [], []
    monkeypatch.setattr(main_window, "set_pipewire_default", defaults.append)

    def reconnect(device):
        starts.append(device)
        if device is None:
            raise RuntimeError("bridge failed")

    window.engine.restart_device = reconnect
    assert MainWindow._select_audio_output(window, "new") is False
    assert defaults == [20, 10]
    assert starts == [None, 2]
    assert window._audio_output_key == "old"


def test_failed_buffer_after_output_change_restores_previous_setup():
    window = window_stub()
    window.audio_buffer = QComboBox()
    window.audio_buffer.addItem("512", 512)
    window.audio_buffer.addItem("256", 256)
    window.audio_buffer.currentIndexChanged.connect(
        lambda _index: window.status.showMessage("unsupported buffer")
    )
    switched = []

    def select(key):
        switched.append(key)
        window._audio_output_key = key
        window.settings.setValue("audio/output_device", key)
        return True

    window._select_audio_output = select
    window.engine.restart_device = lambda _device: None
    dialog = SimpleNamespace(
        output_key="new",
        output_channels=SimpleNamespace(currentData=lambda: (2, 3)),
        recommended_frames=256,
    )
    MainWindow._apply_audio_setup(window, dialog)
    assert switched == ["new", "old"]
    assert window.engine.output_channels == (0, 1)
    assert window._audio_output_key == "old"
    assert window.settings.values["audio/output_device"] == "old"
    assert "audio/setup_complete" not in window.settings.values
    assert "unsupported buffer" in window.status.message


@pytest.mark.parametrize("session_type", [MidiCaptureSession, SamplerCaptureSession])
def test_reused_event_session_starts_with_a_fresh_take(session_type):
    session = session_type("instrument")
    session.arm()
    session.start()
    session.add((60, 0.8))
    assert session.stop() == [(60, 0.8)]
    session.arm()
    session.start()
    session.add((64, 0.7))
    assert session.stop() == [(64, 0.7)]


@pytest.mark.parametrize("hosted", [False, True])
def test_preview_release_preserves_held_keyboard_note(hosted):
    engine = Engine(object())
    if hosted:
        engine.external.instrument = object()
    live = engine.synth_note_on(60)
    preview = engine.synth_preview_note_on(60)
    engine._process_commands()
    voices = engine.external.all_voices() if hosted else engine.synth_voices
    held = next(voice for voice in voices if voice.trigger_id is live)
    audition = next(voice for voice in voices if voice.trigger_id is preview)
    assert engine.arp_state.held == {60}
    assert not held.dead
    engine.synth_preview_note_off(preview)
    engine._process_commands()
    assert engine.arp_state.held == {60}
    assert not held.dead
    if hosted:
        assert audition.dead
        assert not any(event[0][0] == 0x80 for event in engine.external.events)
    else:
        assert held.stage != "release"
        assert audition.stage == "release"


def test_stale_preview_release_does_not_end_a_replacement():
    engine = Engine(object())
    old = engine.synth_preview_note_on(60)
    current = engine.synth_preview_note_on(60)
    engine._process_commands()
    engine.synth_preview_note_off(old)
    engine._process_commands()
    voice = next(voice for voice in engine.synth_voices if voice.trigger_id is current)
    assert voice.stage != "release"
    assert not engine.arp_state.held
