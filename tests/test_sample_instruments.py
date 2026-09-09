"""Chromatic sample sound ownership, UI destinations, persistence and audio."""

from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QMimeData, QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QSignalSpy, QTest

from mpclab.engine import Engine
from mpclab.export import render_export
from mpclab.library import Library
from mpclab.model import Pad, Project, Clip, Row
from mpclab.music import Note
from mpclab.ui import main_window
from mpclab.ui.sample_drag import RANGE_MIME
from mpclab.ui.sequencer import ROW_H, RULER_H, GAP, LABEL_W
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def audio_project(tmp_path):
    library = Library(tmp_path / "audio")
    t = np.arange(24000) / 48000
    tone = (np.sin(2 * np.pi * 220 * t) * 0.15).astype(np.float32)
    source = library.add_audio(np.column_stack((tone, tone)), "A3 tone")
    project = Project(bpm=120)
    project.pads[0] = Pad(
        sample_id=source.id,
        name=source.name,
        end=0.5,
        root_note=57,
        mode="gate",
        attack=0.001,
        release=0.005,
        track=0,
    )
    project.pads[1] = replace(project.pads[0], track=1, root_note=45)
    project.pattern().bars = 1
    project.delay_fx.enabled = project.reverb_fx.enabled = False
    project.rows = [Row(clips=[Clip(ref=project.pattern().id, length_beats=4)])]
    return library, project


def make_engine(library, project):
    engine = Engine(library, blocksize=512)
    engine.project = Project.from_dict(project.to_dict())
    engine.preload_project_audio()
    engine.prepare_fx()
    return engine


def callback(engine, frames=512):
    out = np.zeros((frames, 2), np.float32)
    engine._callback(out, frames, None, False)
    return out


@pytest.mark.parametrize("pitch,rate", [(45, 0.5), (57, 1), (69, 2)])
def test_root_controls_rate_and_duration(audio_project, pitch, rate):
    library, project = audio_project
    voice = make_engine(library, project)._voice_for_pad(project.pads[0], 0.8, pitch)
    assert voice.rate == rate and voice.length == 24000 / rate
    assert voice.track == 0 and voice.note == pitch


@pytest.mark.parametrize("value", [-1, 64, True, 1.5, "0"])
def test_malformed_note_destination_rejected(value):
    with pytest.raises(ValueError):
        Project.from_dict({"patterns": [{"notes": [{"pad": value}]}]})


@pytest.mark.parametrize("value", [-1, 128, True, 60.5, "60"])
def test_malformed_root_rejected(value):
    with pytest.raises(ValueError):
        Project.from_dict({"pads": [{"root_note": value}]})


def test_roundtrip_and_legacy_notes(audio_project, tmp_path):
    _, project = audio_project
    project.pattern().notes = [
        Note(57, 0, 0.5, 0.8, 0),
        Note(64, 1, 0.25, 0.7),
        Note(45, 2, 0.3, 0.6, 1),
    ]
    path = tmp_path / "song.json"
    project.save(path)
    assert Project.load(path).to_dict() == project.to_dict()
    assert project.to_dict()["format_version"] == 4
    legacy = Project.from_dict({"format_version": 2, "patterns": [{"notes": [{"pitch": 67}]}]})
    assert legacy.pattern().notes[0].pad is None and legacy.pads[0].root_note == 60


def test_independent_samples_and_synth(audio_project):
    library, project = audio_project
    project.pattern().notes = [
        Note(57, 0, 0.5, 0.8, 0),
        Note(45, 0, 0.5, 0.8, 1),
        Note(64, 0, 0.5, 0.8),
    ]
    engine = make_engine(library, project)
    engine.playing = True
    callback(engine)
    assert {(v.pad_index, v.note, v.rate, v.track) for v in engine.voices} == {
        (0, 57, 1.0, 0),
        (1, 45, 1.0, 1),
    }
    assert [v.note for v in engine.synth_voices] == [64]


@pytest.mark.parametrize("mono,remaining", [(False, 3), (True, 1)])
def test_chords_and_mono(audio_project, mono, remaining):
    library, project = audio_project
    project.pads[0].mono = mono
    engine = make_engine(library, project)
    for pitch in (57, 61, 64):
        engine.sample_note_on(0, pitch, 0.5)
    callback(engine, 1024)
    assert len(engine.voices) == remaining


def test_release_preserves_other_pitch_and_sequence(audio_project):
    library, project = audio_project
    project.pads[0].mode = "loop"
    engine = make_engine(library, project)
    engine._spawn(project.pads[0], 0, 0.5, note=57, live_trigger=False, sequence_id="backing")
    engine.sample_note_on(0, 57, 0.5)
    engine.sample_note_on(0, 64, 0.5)
    callback(engine)
    engine.sample_note_off(0, 57)
    callback(engine, 1024)
    assert {(v.note, v.live_trigger) for v in engine.voices} == {(57, False), (64, True)}


def test_release_remembers_original_mode(audio_project):
    library, project = audio_project
    project.pads[0].mode = "loop"
    engine = make_engine(library, project)
    engine.sample_note_on(0, 57)
    callback(engine)
    engine.project.pads[0].mode = "one-shot"
    engine.sample_note_off(0, 57)
    callback(engine, 1024)
    assert not engine.voices


@pytest.mark.parametrize("action", ["stop", "seek", "pad_release"])
def test_live_sample_transport_ownership(audio_project, action):
    library, project = audio_project
    project.pads[0].mode = "loop"
    engine = make_engine(library, project)
    engine.sample_note_on(0, 57)
    callback(engine)
    if action == "stop":
        engine.stop_transport()
    elif action == "seek":
        engine.set_position(2)
    else:
        engine.release_pad(0)
    callback(engine, 1024)
    assert len(engine.voices) == 1 and engine.voices[0].live_trigger


@pytest.mark.parametrize("mode", ["song", "pattern"])
def test_live_offline_sample_agreement_at_root(audio_project, mode):
    library, project = audio_project
    project.pattern().notes = [Note(57, 0.25, 0.25, 0.8, 0), Note(45, 1, 0.1, 0.6, 1)]
    offline = make_engine(library, project).render_offline(mode, tail=0)
    live = make_engine(library, project)
    live.mode, live.playing = mode, True
    blocks = [callback(live, min(512, len(offline) - i)) for i in range(0, len(offline), 512)]
    assert np.max(np.abs(offline)) > 0.01
    np.testing.assert_allclose(np.concatenate(blocks), offline, atol=2e-5)
    assert not np.any(offline[40000:])


def test_missing_instrument_export_error(audio_project, tmp_path):
    library, project = audio_project
    project.pattern().notes = [Note(pad=2)]
    with pytest.raises(ValueError, match="Missing sample instrument"):
        render_export(project, library, tmp_path / "out.wav", tail=0)


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    w = main_window.MainWindow(tmp_path, restore_session=False)
    tone = np.sin(np.arange(24000) * 0.05).astype(np.float32) * 0.1
    clip = w.library.add_audio(np.column_stack((tone, tone)), "Tone one")
    w.load_clip_into_editor(clip.id)
    yield w
    w._dirty = False
    w.close()
    # Closing a QWidget only hides it; explicitly dispose its child timers so
    # previous test windows cannot flood later hover tests' event loop.
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def mime_for(w):
    mime = QMimeData()
    mime.setData(RANGE_MIME, f"{w.current_clip}|0.1|0.2".encode())
    return mime


def test_play_notes_undo_redo_and_reopen(window, tmp_path):
    before = window.project.to_dict()
    i = window.sample_workflow.send(window.current_clip, 0.1, 0.2, destination="notes")
    assert i == 0 and window.piano_roll.target_pad == 0
    window.piano_roll.add_chord()
    assert {n.pad for n in window.project.pattern().notes} == {0}
    window.undo()
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    pad = window.project.pads[0]
    assert (pad.start, pad.end, pad.mode) == (0.1, 0.2, "gate")
    path = tmp_path / "sample-project.json"
    window.project.save(path)
    assert Project.load(path).pads[0] == pad


def test_new_channel_does_not_replace_another(window):
    first = window.sample_workflow.send(window.current_clip, destination="notes")
    window.project.pads[first].pitch = -3.5
    window.project.pattern().notes = [Note(pad=first)]
    original = replace(window.project.pads[first])
    second = window.sample_workflow.send(window.current_clip, destination="notes")
    assert second != first and window.project.pads[first] == original
    assert window.project.pattern().notes[0].pad == first


def test_replacement_preserves_rhythm_routing_and_undo(window):
    window.sample_workflow.send(window.current_clip)
    window.project.pattern().steps[0] = {0: 0.8, 3: 0.4}
    window.project.pads[0].track = 5
    window.project.pads[0].gain = 0.25
    before = window.project.to_dict()
    window.sample_workflow.send(window.current_clip, 0.1, 0.2, index=0)
    assert window.project.pattern().steps[0] == {0: 0.8, 3: 0.4}
    assert window.project.pads[0].track == 5 and window.project.pads[0].gain == 0.25
    window.undo()
    assert window.project.to_dict() == before


def test_edits_only_affect_selected_channel(window):
    window.sample_workflow.send(window.current_clip, destination="notes")
    window.project.pattern().notes = [Note(60, 0.18, 0.25, 0.8), Note(60, 0.18, 0.25, 0.8, 0)]
    panel = window.piano_roll
    panel.quantize()
    assert [n.start for n in window.project.pattern().notes] == [0.18, 0.25]
    panel.canvas.selected = panel.canvas.visible_indices()
    panel.canvas.delete_selected()
    assert (
        len(window.project.pattern().notes) == 1 and window.project.pattern().notes[0].pad is None
    )


def test_recording_remembers_keydown_target(window):
    window.sample_workflow.send(window.current_clip, destination="notes")
    window.engine.mode = "pattern"
    window.engine.playing = window.engine.recording = True
    window.engine.beat = 1
    window.play_selected_note(60, 0.7)
    window.piano_roll.select_channel(None)
    window.engine.beat = 1.5
    window.release_selected_note(60)
    assert window.project.pattern().notes == [Note(60, 1, 0.5, 0.7, 0)]
    commands = []
    while not window.engine.cmds.empty():
        commands.append(window.engine.cmds.get_nowait())
    assert ("sampleoff", 0, 60) in commands


def test_steps_to_notes_keeps_swing_and_avoids_duplicate_audio(window):
    window.sample_workflow.send(window.current_clip)
    window.project.swing = 30
    window.project.pattern().steps[0] = {0: 1.0, 2: 0.45, 3: 0.7}
    beats = [
        s / window.project.pattern().div + window.engine._swing_offset(window.project.pattern(), s)
        for s in (0, 2, 3)
    ]
    before = window.project.to_dict()
    window.sample_workflow.step_notes(0)
    assert [n.start for n in window.project.pattern().notes] == beats
    assert [n.velocity for n in window.project.pattern().notes] == [1.0, 0.45, 0.7]
    assert not window.project.pattern().steps
    window.sample_workflow.step_notes(0)
    assert len(window.project.pattern().notes) == 3
    window.undo()
    assert window.project.to_dict() == before


@pytest.mark.parametrize("studio", [False, True])
@pytest.mark.parametrize("target", [1, 6])
def test_tab_hover_and_direct_drop(window, studio, target):
    if studio:
        window.studio.select(0)
        widget = window.studio.buttons[target]
        point = QPoint(10, 10)
    else:
        window.show_tab(0)
        widget = window.tabs.tabBar()
        point = widget.tabRect(target).center()
    # Settle pending layout work before timing this interaction. Keep the
    # product's 350 ms delay, but observe the timeout instead of assuming a
    # shared CI runner dispatches every Qt event within a 50 ms margin.
    QCoreApplication.processEvents()
    timer = window._arrange_drop_filter.timer
    assert timer.interval() == 350
    fired = QSignalSpy(timer.timeout)
    mime = mime_for(window)
    enter = QDragEnterEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window._arrange_drop_filter.eventFilter(widget, enter)
    assert enter.isAccepted()
    assert window._arrange_drop_filter.pending == (target, widget is not window.tabs.tabBar())
    assert fired.count() == 0 and all(p.empty for p in window.project.pads)
    assert fired.wait(1000)
    assert fired.count() == 1
    assert window.studio.selected == target
    assert window.tabs.currentIndex() == 8
    assert all(p.empty for p in window.project.pads)
    drop = QDropEvent(QPointF(point), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window._arrange_drop_filter.eventFilter(widget, drop)
    assert drop.isAccepted()
    assert (window.project.pads[0].start, window.project.pads[0].end) == (0.1, 0.2)
    assert len(window._undo) == 1


def test_lane_drop_and_add_area(window):
    window.sample_workflow.send(window.current_clip)
    window.project.pattern().steps[0] = {0: 0.7}
    grid = window.step_grid
    for point, expected in [
        (QPointF(10, RULER_H + 10), 0),
        (QPointF(LABEL_W + 10, RULER_H + len(grid.lanes()) * (ROW_H + GAP) + 10), 1),
    ]:
        mime = mime_for(window)
        drop = QDropEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        grid._sample_drop.eventFilter(grid, drop)
        assert drop.isAccepted() and window.project.pads[expected].sample_id == window.current_clip
    assert window.project.pattern().steps[0] == {0: 0.7}


def test_invalid_cancelled_and_full_drops_leave_music_unchanged(window):
    before = window.project.to_dict()
    window.sample_workflow.send(window.current_clip, float("nan"), 0.2)
    assert window.project.to_dict() == before and not window._undo
    for i in range(64):
        window.project.pattern().notes.append(Note(pad=i))
    before = window.project.to_dict()
    assert window.sample_workflow.send(window.current_clip) is None
    assert window.project.to_dict() == before
    window._arrange_drop_filter.pending = (1, True)
    window._arrange_drop_filter.eventFilter(window.studio.buttons[1], QEvent(QEvent.DragLeave))
    assert window._arrange_drop_filter.pending is None


@pytest.mark.parametrize("pitch,expected", [(45, 110), (57, 220), (69, 440)])
def test_rendered_sample_frequency(audio_project, pitch, expected):
    library, project = audio_project
    engine = make_engine(library, project)
    voice = engine._voice_for_pad(project.pads[0], 0.8, pitch)
    audio = np.zeros((8192, 2), np.float32)
    voice.render(audio, 0)
    spectrum = np.abs(np.fft.rfft(audio[:, 0] * np.hanning(len(audio))))
    frequency = np.argmax(spectrum) * engine.sr / len(audio)
    assert abs(frequency - expected) < engine.sr / len(audio)


@pytest.mark.parametrize("mode,alive", [("one-shot", True), ("gate", False), ("loop", False)])
def test_note_release_modes(audio_project, mode, alive):
    library, project = audio_project
    project.pads[0].mode = mode
    engine = make_engine(library, project)
    engine.sample_note_on(0, 57)
    callback(engine)
    engine.sample_note_off(0, 57)
    callback(engine, 1024)
    assert bool(engine.voices) is alive


def test_failed_decode_preserves_document_and_history(window, monkeypatch):
    before = window.project.to_dict()

    def failed(*args):
        raise OSError("synthetic unavailable source")

    monkeypatch.setattr(window.library, "audio", failed)
    assert window.sample_workflow.send(window.current_clip) is None
    assert window.project.to_dict() == before and not window._undo


def test_notes_header_drop_replaces_only_selected_sound(window):
    for _ in range(2):
        window.sample_workflow.send(window.current_clip, destination="notes")
    old = replace(window.project.pads[0])
    window.project.pattern().notes = [Note(pad=0), Note(pad=1)]
    mime = mime_for(window)
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.piano_roll._sound_drop.eventFilter(window.piano_roll.channel, drop)
    assert drop.isAccepted() and window.project.pads[0] == old
    assert window.project.pads[1].start == 0.1
    assert [note.pad for note in window.project.pattern().notes] == [0, 1]


def test_project_change_discards_held_sample_ownership(window):
    window.sample_workflow.send(window.current_clip, destination="notes")
    window.play_selected_note(60)
    window._apply_project(Project())
    window.release_selected_note(60)
    assert not window.sample_workflow.held
    assert not window.project.pattern().notes


def test_selected_bass_octave_is_visible(window):
    window.sample_workflow.send(window.current_clip, destination="notes")
    window.project.pads[0].root_note = 33
    window.project.pattern().notes = [Note(33, 0, 1, 0.8, 0), Note(40, 1, 1, 0.8, 0)]
    window.resize(1440, 900)
    window.show()
    window.piano_roll.select_channel(0)
    QTest.qWait(30)
    panel = window.piano_roll
    top = panel.scroll.verticalScrollBar().value()
    bottom = top + panel.scroll.viewport().height()
    for note in window.project.pattern().notes:
        assert top <= panel.canvas.rect_for(note).center().y() <= bottom


def test_runtime_voices_use_identity_not_array_equality(audio_project):
    library, project = audio_project
    engine = make_engine(library, project)
    first = engine._voice_for_pad(project.pads[0], 0.8, 57)
    second = engine._voice_for_pad(project.pads[0], 0.8, 57)
    assert first is not second
    assert first != second
    assert first not in [second]


def test_disarming_record_finishes_sample_note_at_disarm_time(window):
    window.sample_workflow.send(window.current_clip, destination="notes")
    window.engine.mode = "pattern"
    window.engine.playing = window.engine.recording = True
    window.engine.beat = 1
    window.play_selected_note(60, 0.7)
    window.engine.beat = 1.5
    window._record_toggled(False)
    window.engine.beat = 2.5
    window.release_selected_note(60)
    assert window.project.pattern().notes == [Note(60, 1, 0.5, 0.7, 0)]
    assert not window.sample_workflow.recorded
