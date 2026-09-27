"""Exercise the full input path: one hit must produce one musical event."""

import numpy as np
import pytest

from mpclab.model import SynthPatch
from tests.test_product_hardening_ui import window  # noqa: F401


def prepare(app, workspace):
    clip = app.library.add_audio(np.full((4800, 2), 0.1, np.float32), "Pad test")
    app.sample_workflow.send(clip.id, index=0, destination="beats")
    app.show_tab(workspace)
    app.engine.mode = "pattern"
    app.engine.playing = app.engine.recording = True
    app.engine.beat = 0.5
    app.engine.audio_clock = (10, 0.5, 120, True)
    app.engine.midi.source = None
    app.devices._tick()
    return app.project.pattern()


def midi(app, message, stamp):
    app.engine.midi.submit("mpc", message, stamp)
    app.engine.midi.process(128, stamp)
    app.engine._process_commands()


@pytest.mark.parametrize("workspace", ["TAB_SEQ", "TAB_PIANO"])
def test_gui_pad_hit_records_one_step_and_no_instrument_note(window, workspace):  # noqa: F811
    pattern = prepare(window, getattr(window, workspace))
    window._pad_pressed(0, 0.8)
    window.engine._process_commands()
    window.engine.beat = 0.75
    window._pad_released(0)
    assert pattern.steps == {0: {2: 0.8}}
    assert pattern.notes == []
    events, _ = window.engine._collect(0, 1)
    assert len(events) == 1


@pytest.mark.parametrize("workspace", ["TAB_SEQ", "TAB_PIANO"])
def test_mpc_pad_hit_records_one_step_and_no_piano_note(window, workspace):  # noqa: F811
    pattern = prepare(window, getattr(window, workspace))
    window.devices.router.settings["mpc"] = {"mode": "Pads", "pad_base": 36}
    midi(window, [0x90, 36, 100], 10)
    midi(window, [0x80, 36, 0], 10.1)
    assert len(pattern.steps[0]) == 1
    assert pattern.notes == []
    assert len(window.engine._collect(0, 1)[0]) == 1


@pytest.mark.parametrize("workspace", ["TAB_SEQ", "TAB_PIANO", "TAB_SYNTH"])
def test_midi_key_records_once_to_selected_instrument(window, workspace):  # noqa: F811
    pattern = prepare(window, getattr(window, workspace))
    instrument = window.project.add_instrument("Keys", SynthPatch(track=3))
    window.piano_roll.select_channel(instrument.id)
    window.devices._tick()
    midi(window, [0x90, 60, 100], 10)
    midi(window, [0x80, 60, 0], 10.2)
    assert len(pattern.notes) == 1
    note = pattern.notes[0]
    assert note.instrument == instrument.id and note.pad is None
    assert not pattern.steps
    assert note.start == pytest.approx(0.5)
    assert note.duration == pytest.approx(0.4)


def test_count_in_clicks_when_metronome_is_off_without_recording_clicks(window, monkeypatch):  # noqa: F811
    from mpclab.ui import window_transport

    window.show_tab(window.TAB_SEQ)
    window.project.bpm = 120
    now = [10.0]
    monkeypatch.setattr(window_transport.time, "monotonic", lambda: now[0])
    clicks = []
    monkeypatch.setattr(window.engine, "_click", lambda offset, accent: clicks.append(accent))
    window.btn_rec.click()
    window.engine._process_commands()
    assert clicks == [True]
    for tick in (10.5, 11.0):
        now[0] = tick
        window._advance_record_count()
        window.engine._process_commands()
    assert clicks == [True, False, False]
    assert not window.engine.playing and not window.engine.recording
    now[0] = 11.5
    window._advance_record_count()
    window.engine._process_commands()
    assert window.engine.playing and window.engine.recording
    assert clicks[-1] is True
    assert not window.project.pattern().steps and not window.project.pattern().notes
    assert not window.engine.metronome


@pytest.mark.parametrize("workspace", ["TAB_SEQ", "TAB_PIANO"])
def test_record_button_stays_in_workspace_and_accepts_mpc_and_typing(
    window,  # noqa: F811
    monkeypatch,
    workspace,
):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from mpclab.ui import window_transport

    pattern = prepare(window, getattr(window, workspace))
    window.engine.playing = window.engine.recording = False
    instrument = window.project.add_instrument("Keyboard layer", SynthPatch(track=3))
    window.piano_roll.select_channel(instrument.id)
    window.devices._tick()
    window.devices.router.settings["mpc"] = {"mode": "Pads"}
    now = [10.0]
    monkeypatch.setattr(window_transport.time, "monotonic", lambda: now[0])
    window.btn_rec.click()
    now[0] = 12.0
    window._advance_record_count()
    window.engine._process_commands()
    assert window.studio.selected == getattr(window, workspace)
    assert window.engine.recording and window.engine.playing
    midi(window, [0x99, 36, 100], 10)
    midi(window, [0x89, 36, 0], 10.1)
    window.toggle_typing_keyboard()
    keyboard = window.typing_keyboard
    window.engine.beat = 1
    QTest.keyPress(keyboard.keyboard, Qt.Key_Z)
    window.engine._process_commands()
    window.engine.beat = 1.5
    QTest.keyRelease(keyboard.keyboard, Qt.Key_Z)
    window.stop_all()
    assert sum(len(row) for row in pattern.steps.values()) == 1
    assert len(pattern.notes) == 1
    assert pattern.notes[0].instrument == instrument.id
    assert pattern.notes[0].pad is None


def test_cancel_count_in_silences_click_and_does_not_start_recording(window):  # noqa: F811
    from mpclab.engine import METRONOME

    window.show_tab(window.TAB_SEQ)
    window.btn_rec.click()
    window.engine._process_commands()
    assert any(v.pad_index == METRONOME for v in window.engine.voices)
    window.btn_rec.click()
    window.engine._process_commands()
    window._advance_record_count()
    assert not any(v.pad_index == METRONOME for v in window.engine.voices)
    assert not window.engine.playing and not window.engine.recording


def test_count_in_click_renders_audio_while_transport_stopped(window):  # noqa: F811
    output = np.zeros((window.engine.blocksize, 2), np.float32)
    window.engine.count_in_click(True)
    window.engine._callback(output, len(output), None, None)
    assert np.max(np.abs(output)) > 0.01
    assert not window.engine.playing
    assert window.engine.beat == 0
    assert not window.project.pattern().steps and not window.project.pattern().notes


@pytest.mark.parametrize("workspace", ["TAB_SEQ", "TAB_PIANO"])
@pytest.mark.parametrize("source", ["mpc", "gui"])
def test_first_pad_hit_at_count_in_deadline_survives_delayed_ui_timer(
    window,  # noqa: F811
    monkeypatch,
    workspace,
    source,
):
    from mpclab.ui import window_transport

    now = [10.0]
    monkeypatch.setattr(window_transport.time, "monotonic", lambda: now[0])
    pattern = prepare(window, getattr(window, workspace))
    window.engine.playing = window.engine.recording = False
    window.engine.beat = 0
    window.project.bpm = 120
    window.devices.router.settings["mpc"] = {"mode": "Pads", "pad_base": 36}
    window.btn_rec.click()
    window.engine._process_commands()
    now[0] = window._record_count_deadline
    if source == "mpc":
        window.engine.midi.submit("mpc", [0x99, 36, 100], now[0])
    else:
        window._pad_pressed(0, 100 / 127)
    output = np.zeros((window.engine.blocksize, 2), np.float32)
    # No GUI timer tick: a busy paint must not hold the audio recorder back.
    window.engine._callback(output, len(output), None, None)
    assert pattern.steps == {0: {0: pytest.approx(100 / 127, abs=0.001)}}
    assert pattern.notes == []
    assert window.engine.playing and window.engine.recording
    beat = window.engine.beat
    window._advance_record_count()
    window.engine._process_commands()
    assert window.engine.beat == beat


@pytest.mark.parametrize("workspace", ["TAB_SEQ", "TAB_PIANO"])
def test_first_keyboard_note_at_deadline_is_saved_before_ui_timer(window, monkeypatch, workspace):  # noqa: F811
    from mpclab.ui import window_transport

    now = [10.0]
    monkeypatch.setattr(window_transport.time, "monotonic", lambda: now[0])
    pattern = prepare(window, getattr(window, workspace))
    window.engine.playing = window.engine.recording = False
    window.engine.beat = 0
    window.btn_rec.click()
    window.engine._process_commands()
    now[0] = window._record_count_deadline
    window.play_synth_note(60, 0.8)
    output = np.zeros((window.engine.blocksize, 2), np.float32)
    window.engine._callback(output, len(output), None, None)
    window.release_synth_note(60)
    assert len(pattern.notes) == 1
    assert pattern.notes[0].start == 0
    assert not pattern.steps
