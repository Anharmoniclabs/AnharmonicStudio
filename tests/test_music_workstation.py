"""Musical behavior across persistence, the callback, editors, and rendering."""

from dataclasses import replace
import threading

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from mpclab.engine import Engine
from mpclab.export import ExportJob, ExportCancelled, render_export
from mpclab.library import Library
from mpclab.model import Project, Row, Clip
from mpclab.music import Note, AutomationLane, AutomationPoint
from mpclab.ui.main_window import MainWindow


def musical_project():
    project = Project(bpm=120)
    project.pattern().bars = 1
    project.pattern().notes = [Note(60, 0.25, 0.5, 0.8), Note(67, 1, 0.75, 0.65)]
    project.synth = replace(project.synth, noise=0, release=0.02, volume=0.2)
    project.rows = [
        Row(clips=[Clip(kind="pattern", ref=project.pattern().id, start_beat=0, length_beats=4)])
    ]
    project.song_length_beats = 4
    return project


def engine_for(project, tmp_path):
    engine = Engine(Library(tmp_path / "audio"), sample_rate=8000, blocksize=512)
    engine.project = Project.from_dict(project.to_dict())
    engine.prepare_fx()
    return engine


def test_notes_and_envelopes_round_trip_and_legacy_loads(tmp_path):
    project = musical_project()
    project.automation = [AutomationLane(points=[AutomationPoint(0, 0), AutomationPoint(4, 1)])]
    path = tmp_path / "session.json"
    project.save(path)
    loaded = Project.load(path)
    assert loaded.to_dict() == project.to_dict()
    assert Project.from_dict({"format_version": 1}).pattern().notes == []


@pytest.mark.parametrize(
    "note",
    [
        dict(pitch=128),
        dict(start=-1),
        dict(duration=0),
        dict(velocity=float("nan")),
        dict(pitch=True),
    ],
)
def test_invalid_notes_fail_safely(note):
    with pytest.raises(ValueError):
        Project.from_dict({"patterns": [{"notes": [note]}]})


@pytest.mark.parametrize(
    "lane",
    [
        dict(target="plugin:missing"),
        dict(points=[dict(beat=0, value=9)]),
        dict(points=[dict(beat=0, value=1), dict(beat=0, value=0)]),
    ],
)
def test_invalid_automation_fails_safely(lane):
    with pytest.raises(ValueError):
        Project.from_dict({"automation": [lane]})


def test_note_repetition_gate_clipping_and_row_mute(tmp_path):
    project = musical_project()
    project.pattern().notes = [Note(60, 1, 4)]
    project.rows[0].clips[0].length_beats = 6
    engine = engine_for(project, tmp_path)
    engine.mode = "song"
    events, _ = engine._collect(0, 8)
    assert [event[:4] for event in events] == [(1, -61, 0.8, 3.0), (5, -61, 0.8, 1.0)]
    engine.project.rows[0].mute = True
    assert engine._collect(0, 8)[0] == []


@pytest.mark.parametrize("inserts", [False, True])
def test_synth_and_automation_match_live_and_offline(tmp_path, inserts):
    project = musical_project()
    if inserts:
        project.tracks[2].fx.low = 6
        project.tracks[2].fx.comp = True
    project.automation = [
        AutomationLane("master", [AutomationPoint(0, 0.4), AutomationPoint(4, 1)]),
        AutomationLane("track:2:pan", [AutomationPoint(0, -1), AutomationPoint(4, 1)]),
        AutomationLane(
            "track:2:gain",
            [AutomationPoint(0, 0), AutomationPoint(0.5, 0), AutomationPoint(4, 0.7)],
        ),
    ]
    offline = engine_for(project, tmp_path).render_offline("song", tail=0)
    live = engine_for(project, tmp_path)
    live.mode = "song"
    live.playing = True
    blocks = []
    for start in range(0, len(offline), 512):
        out = np.zeros((min(512, len(offline) - start), 2), dtype=np.float32)
        live._callback(out, len(out), None, False)
        blocks.append(out)
    rendered = np.concatenate(blocks)
    assert np.max(np.abs(offline)) > 0.01
    assert not np.any(offline[:1000])  # first note begins at beat 0.25
    np.testing.assert_allclose(rendered, offline, atol=2e-5)


def test_automation_bypass_and_pattern_mode_do_not_override_manual_faders(tmp_path):
    project = musical_project()
    project.automation = [AutomationLane("master", [AutomationPoint(0, 0)])]
    assert not np.any(engine_for(project, tmp_path).render_offline("song", tail=0))
    assert np.any(engine_for(project, tmp_path).render_offline("pattern", tail=0))
    project.automation[0].enabled = False
    assert np.any(engine_for(project, tmp_path).render_offline("song", tail=0))


def test_step_envelope_holds_and_linear_interpolates():
    lane = AutomationLane(points=[AutomationPoint(1, 0), AutomationPoint(3, 1)])
    np.testing.assert_allclose(lane.values([0, 1, 2, 3, 4]), [0, 0, 0.5, 1, 1])
    lane.interpolation = "step"
    np.testing.assert_allclose(lane.values([0, 1, 2, 3, 4]), [0, 0, 0, 1, 1])


def test_stop_releases_sequenced_notes(tmp_path):
    engine = engine_for(musical_project(), tmp_path)
    engine._spawn_synth(60, 1, gate_frames=100000, live_trigger=False)
    engine.stop_transport()
    engine._callback(np.zeros((512, 2), dtype=np.float32), 512, None, False)
    assert all(v.stage == "release" for v in engine.synth_voices)


def test_export_snapshot_is_independent_and_cancellation_keeps_destination(tmp_path):
    project = musical_project()
    library = Library(tmp_path / "library")
    out = tmp_path / "mix.wav"
    job = ExportJob(project, library, out)
    project.pattern().notes.clear()
    project.master = 0
    assert len(job.project.pattern().notes) == 2
    assert job.project.master > 0
    out.write_bytes(b"existing export")
    cancel = threading.Event()

    def stop(_):
        cancel.set()

    with pytest.raises(ExportCancelled):
        render_export(job.project, job.library, out, cancel=cancel, progress=stop)
    assert out.read_bytes() == b"existing export"
    assert not list(tmp_path.glob(".render-*"))


def test_export_process_cancellation_preserves_existing_destination(tmp_path):
    project = musical_project()
    project.song_length_beats = 1000
    destination = tmp_path / "existing.wav"
    destination.write_bytes(b"previous take")
    job = ExportJob(project, Library(tmp_path / "library"), destination, tail=0)
    outcomes = []
    # Run the supervisor on this test thread so its progress signal can
    # synchronously request cooperative cancellation in the child process.
    job.progress.connect(lambda value: job.cancel())
    job.cancelled.connect(lambda: outcomes.append("cancelled"))
    job.failed.connect(lambda error: outcomes.append(error))
    job._run()
    assert outcomes == ["cancelled"]
    assert destination.read_bytes() == b"previous take"
    assert not list(tmp_path.glob(".render-*"))


def test_export_process_keeps_cached_sample_after_source_removed(tmp_path):
    from mpclab.library import Clip as LibraryClip

    library = Library(tmp_path / "library")
    reference = "123456abcdef"
    library.clips[reference] = LibraryClip(
        id=reference, name="cached", source_path=str(tmp_path / "removed.wav")
    )
    wave = np.sin(np.arange(48000) * 0.02).astype(np.float32) * 0.1
    library._audio[reference] = np.column_stack((wave, wave))
    project = musical_project()
    project.pattern().notes.clear()
    project.pattern().steps[0] = {0: 1}
    project.pads[0].sample_id = reference
    project.pads[0].end = 1
    destination = tmp_path / "cached.wav"
    job = ExportJob(project, library, destination, tail=0)
    outcomes = []
    job.failed.connect(outcomes.append)
    job._run()
    assert outcomes == []
    data, _ = sf.read(destination)
    assert np.max(np.abs(data)) > 0.01


def test_export_writes_real_audio_and_requested_subtype(tmp_path):
    project = musical_project()
    out = tmp_path / "mix.wav"
    seconds = render_export(project, Library(tmp_path / "library"), out, tail=0, subtype="FLOAT")
    data, sr = sf.read(out)
    assert seconds == pytest.approx(2)
    assert sr == 48000 and sf.info(out).subtype == "FLOAT"
    assert np.max(np.abs(data)) > 0.01
    assert not list(tmp_path.glob(".render-*"))


def test_mid_render_cancellation_removes_temporary_audio(tmp_path):
    destination = tmp_path / "mix.wav"
    destination.write_bytes(b"previous mix")
    cancel = threading.Event()

    def progress(value):
        if value > 0.1:
            cancel.set()

    with pytest.raises(ExportCancelled):
        render_export(
            musical_project(),
            Library(tmp_path / "library"),
            destination,
            cancel=cancel,
            progress=progress,
            tail=0,
        )
    assert destination.read_bytes() == b"previous mix"
    assert not list(tmp_path.glob(".render-*"))


def test_missing_audio_export_fails_without_publishing(tmp_path):
    project = musical_project()
    project.pads[0].sample_id = "deadbeef0123"
    destination = tmp_path / "mix.wav"
    with pytest.raises(ValueError, match="Missing audio"):
        render_export(project, Library(tmp_path / "library"), destination)
    assert not destination.exists()


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = MainWindow(tmp_path, restore_session=False)
    yield window
    window._dirty = False
    window.close()


def test_piano_chord_quantize_undo_and_duplicate_are_connected(window):
    panel = window.piano_roll
    panel.add_chord()
    assert [n.pitch for n in window.project.pattern().notes] == [60, 64, 67]
    window.undo()
    assert window.project.pattern().notes == []
    window.redo()
    assert len(window.project.pattern().notes) == 3
    window.project.pattern().notes[0].start = 0.18
    panel.quantize()
    assert window.project.pattern().notes[0].start == 0.25
    original = window.project.pattern()
    window.dup_pattern()
    assert window.project.pattern().notes == original.notes
    assert window.project.pattern().notes[0] is not original.notes[0]
    window.double_pattern()
    assert len(window.project.pattern().notes) == 6


def test_piano_mouse_draw_resize_and_delete(window):
    window.show_tab(window.TAB_PIANO)
    panel = window.piano_roll
    canvas = panel.canvas
    position = canvas.rect_for(Note(60, 1)).center().toPoint()
    QTest.mouseClick(canvas, Qt.LeftButton, pos=position)
    assert len(window.project.pattern().notes) == 1
    note = window.project.pattern().notes[0]
    assert note.pitch == 60
    panel.duration.setValue(2)
    assert note.duration == 2
    QTest.keyClick(canvas, Qt.Key_Delete)
    assert window.project.pattern().notes == []


def test_automation_undo_and_mixer_read_indication(window):
    panel = window.automation_panel
    panel.value.setValue(0.4)
    panel.set_point()
    assert window.project.automation[0].points[0].value == 0.4
    window.undo()
    assert window.project.automation == []
    window.redo()
    assert len(window.project.automation) == 1
    window.engine.mode = "song"
    master = window.mixer.master_strip
    master.update_meter()
    assert master.fader.value() == 40
    assert not master.fader.isEnabled()
    assert window.project.master == 0.85
    panel.enabled.setChecked(False)
    master.update_meter()
    assert master.fader.isEnabled()
    assert master.fader.value() == 85


def test_played_synth_notes_record_into_pattern(window):
    window.engine.recording = window.engine.playing = True
    window.engine.beat = 1.5
    window.play_synth_note(60, 0.7)
    window.engine.beat = 2.25
    window.release_synth_note(60)
    assert window.project.pattern().notes == [Note(60, 1.5, 0.75, 0.7)]
    window.undo()
    assert window.project.pattern().notes == []


def test_background_export_allows_project_changes_without_changing_live_mode(window, tmp_path):
    project = musical_project()
    window._apply_project(project)
    window.engine.mode = "pattern"
    output = tmp_path / "snapshot.wav"
    assert window.start_export(output, mode="song", tail=0)
    window.project.pattern().notes.clear()
    window.project.master = 0
    for _ in range(500):
        # QTest.qWait can retain the GIL while sleeping. Join releases it so
        # the Python render worker can run, as it does under the Qt event loop.
        if window.export_job is not None:
            window.export_job.thread.join(timeout=0.02)
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        if window.export_job is None:
            break
    assert window.export_job is None
    assert window.engine.mode == "pattern"
    assert window.project.master == 0
    data, _ = sf.read(output)
    assert np.max(np.abs(data)) > 0.01
