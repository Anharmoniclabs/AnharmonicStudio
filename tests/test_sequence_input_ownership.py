"""Editing and auditioning notes must preserve other playback owners."""

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from mpclab.ui.sequencer import LABEL_W, RULER_H
from mpclab.ui import sequencer

from test_track_recording import window as window


@pytest.mark.parametrize("independent_source", [False, True])
def test_notes_channel_switch_releases_input_but_preserves_backing(window, independent_source):
    engine, project = window.engine, window.project
    first = project.add_instrument("First", project.synth)
    second = project.add_instrument("Second", project.synth)
    old = first.id if independent_source else None
    project.selected_instrument = old
    window.piano_roll.target_instrument = old
    window.piano_roll.target_pad = None
    engine.playing = True
    engine.beat = 2.0
    engine._spawn_synth(48, 0.5, gate_frames=48000, live_trigger=False, instrument_id=old)
    backing = engine.synth_voices[-1]
    engine._spawn_synth(55, 0.5, gate_frames=48000, live_trigger=False, instrument_id=second.id)
    other_backing = engine.synth_voices[-1]
    window.sample_workflow.note_on(60, 0.7)
    engine._process_commands()
    live = engine.synth_voices[-1]
    window.piano_roll.canvas.drag = ("audition", 60)

    window.piano_roll.select_channel(second.id)
    engine._process_commands()

    assert live.stage == "release"
    assert backing.stage != "release"
    assert other_backing.stage != "release"
    assert backing.gate_frames == other_backing.gate_frames == 48000
    assert engine.playing and engine.beat == 2.0
    assert project.selected_instrument == second.id
    assert not window.sample_workflow.held
    assert window.piano_roll.canvas.drag is None
    assert not window._undo
    output = np.zeros((512, 2), dtype=np.float32)
    engine._callback(output, len(output), None, False)
    assert not backing.dead and not other_backing.dead
    assert np.max(np.abs(output)) > 0


def test_drawing_a_step_remains_one_undo_transaction(window):
    grid = window.step_grid
    grid.refresh()
    pad = grid.lanes()[0]
    before = window.project.to_dict()

    QTest.mouseClick(grid, Qt.LeftButton, pos=QPoint(LABEL_W + 10, RULER_H + 10))

    assert window.project.pattern().get(pad, 0) == 1.0
    assert len(window._undo) == 1
    after = window.project.to_dict()
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.to_dict() == after


def prepare_pad(window, mode):
    source = window.library.add_audio(np.full((48000, 2), 0.05, np.float32), "Synthetic pad")
    pad = window.project.pads[0]
    pad.sample_id, pad.end, pad.mode = source.id, source.duration, mode
    return pad


@pytest.mark.parametrize("mode", ["gate", "loop"])
def test_beats_audition_has_a_bounded_release(window, monkeypatch, mode):
    prepare_pad(window, mode)
    callbacks = []
    monkeypatch.setattr(
        sequencer.QTimer, "singleShot", lambda ms, ctx, cb: callbacks.append((ms, cb))
    )

    window.step_grid.padAuditioned.emit(0)
    window.engine._process_commands()
    preview = window.engine.voices[-1]
    original_length = preview.length
    assert len(callbacks) == 1 and callbacks[0][0] == 300
    callbacks[0][1]()
    window.engine._process_commands()

    assert preview.length < original_length
    assert preview.length <= preview.age + preview.release
    assert not window._undo


@pytest.mark.parametrize("new_owner", ["physical", "midi", "preview"])
def test_stale_beats_release_cannot_stop_a_new_hit(window, monkeypatch, new_owner):
    prepare_pad(window, "gate")
    callbacks = []
    monkeypatch.setattr(sequencer.QTimer, "singleShot", lambda ms, ctx, cb: callbacks.append(cb))
    window.step_grid.padAuditioned.emit(0)
    window.engine._process_commands()
    if new_owner == "physical":
        window._pad_pressed(0, 0.8)
    elif new_owner == "midi":
        window.engine.midi._pad_on(0, 0.8)
    else:
        window.step_grid.padAuditioned.emit(0)
    window.engine._process_commands()
    current = window.engine.voices[-1]
    current_length = current.length

    callbacks[0]()
    window.engine._process_commands()

    assert current.length == current_length
    if new_owner == "physical":
        window._pad_released(0)
    elif new_owner == "midi":
        window.engine.midi._pad_off(0)
    else:
        callbacks[1]()
    window.engine._process_commands()
    assert current.length < current_length


def test_beats_audition_does_not_record_an_extra_step(window, monkeypatch):
    prepare_pad(window, "one-shot")
    callbacks = []
    monkeypatch.setattr(sequencer.QTimer, "singleShot", lambda *args: callbacks.append(args))
    window.engine.playing = window.engine.recording = True
    window.engine.beat = 1.0
    before = window.project.to_dict()

    window.step_grid.padAuditioned.emit(0)
    window.engine._process_commands()

    assert window.engine.voices
    assert window.project.to_dict() == before
    assert window.engine.playing and window.engine.recording
    assert not window._undo
    assert not callbacks


def test_notes_channel_refresh_preserves_sample_target(window):
    prepare_pad(window, "gate")
    window.piano_roll.select_channel(0)
    second = window.project.add_instrument("Second", window.project.synth)
    window.project.selected_instrument = second.id

    window.piano_roll.sync_channels()

    assert window.piano_roll.target_pad == 0
    assert window.piano_roll.target_instrument is None
    assert window.piano_roll.channel.currentData() == 0
