from pathlib import Path
from types import SimpleNamespace
import threading
import time

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtWidgets import QApplication

from mpclab.model import Project, Clip, Row
from mpclab.music import Note
from mpclab.scoring import collect_score, musicxml
from mpclab.transcription import (
    DetectedNote, DetectedPart, Transcription, TranscriptionCancelled,
    transcribe_file, decode_activations, FRAME_SECONDS,
)
from mpclab.transcription_project import transcribed_project
from mpclab.ui.transcription import TranscriptionJob, TranscriptionDialog


@pytest.fixture
def musical_audio(tmp_path):
    """Known polyphonic audio, generated without using the transcription model."""
    rate = 22050
    audio = np.zeros(rate * 6, dtype=np.float32)
    for start, pitches in ((1, [60, 64, 67]), (3, [62]), (4, [69])):
        times = np.arange(int(rate * .75)) / rate
        envelope = np.minimum(1, times / .02) * np.minimum(1, (.75 - times) / .08)
        for pitch in pitches:
            frequency = 440 * 2 ** ((pitch - 69) / 12)
            wave = sum(np.sin(2 * np.pi * frequency * harmonic * times) / harmonic ** 1.5
                       for harmonic in range(1, 5)) * .12 * envelope
            audio[start * rate:start * rate + len(wave)] += wave
    path = tmp_path / "chord-and-melody.wav"
    sf.write(path, audio, rate)
    return path


def test_real_model_recovers_chord_melody_and_silence(musical_audio, tmp_path):
    result = transcribe_file(musical_audio, mode="single", bpm=120)
    assert result.bpm == 120 and result.duration == 6
    notes = result.parts[0].notes
    assert [note.pitch for note in notes] == [60, 64, 67, 62, 69]
    for note, onset in zip(notes, [1, 1, 1, 3, 4]):
        assert abs(note.start - onset) < .08
        assert abs(note.end - (onset + .75)) < .08
    silence = tmp_path / "silence.wav"
    sf.write(silence, np.zeros(22050), 22050)
    assert transcribe_file(silence, mode="single").note_count == 0


def test_decoder_keeps_chords_repeated_notes_and_long_sustains():
    frames = np.zeros((400, 88), np.float32)
    onsets = np.zeros_like(frames)
    frames[20:300, 39] = .9
    frames[20:90, 43] = .8
    onsets[20, 39] = .9
    onsets[160, 39] = .9
    notes = decode_activations(frames, onsets, 400 * FRAME_SECONDS)
    assert [(n.pitch, round(n.start / FRAME_SECONDS), round(n.end / FRAME_SECONDS))
            for n in notes] == [(60, 20, 160), (64, 20, 90), (60, 160, 300)]


def test_cancel_and_invalid_audio_never_return_a_score(musical_audio, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(TranscriptionCancelled):
        transcribe_file(musical_audio, mode="single", cancel=cancel)
    bad = tmp_path / "bad.wav"
    bad.write_text("not audio")
    with pytest.raises(ValueError):
        transcribe_file(bad, mode="single")
    with pytest.raises(ValueError, match="Tempo"):
        transcribe_file(musical_audio, bpm=float("nan"))


def example_result(duration=6):
    return Transcription("Example", duration, 120, [
        DetectedPart("Piano", [DetectedNote(60, 1, 1.75, .9), DetectedNote(64, 1, 1.75, .8)]),
        DetectedPart("Kick", [DetectedNote(36, 1, 1.1, .8)], True)], "song4")


def test_import_is_additive_preserves_timing_and_round_trips(tmp_path):
    original = Project(bpm=90)
    original.pattern().notes = [Note(72, 0, 1)]
    original.rows[0].clips = [Clip(ref=original.pattern().id)]
    before = original.to_dict()
    result = example_result()
    project = transcribed_project(original, result, start_beat=8)
    assert original.to_dict() == before
    assert project.bpm == 120
    assert project.patterns[0].notes[0].pitch == 72
    assert all(row.mute for row in project.rows[-2:])
    assert project.rows[-2].clips[0].start_beat == 8
    assert project.patterns[-2].notes[0].start == 2
    assert project.patterns[-2].notes[0].duration == 1.5
    assert project.patterns[-1].notes[0].channel == 9
    assert len({n.instrument for p in project.patterns[1:] for n in p.notes}) == 2
    path = tmp_path / "score.json"
    project.save(path)
    loaded = Project.load(path)
    assert loaded.to_dict() == project.to_dict()
    score = collect_score(loaded, song=True)
    assert any(part.percussion for part in score.parts)
    assert "unpitched" in musicxml(score)


def test_long_import_splits_at_persistable_pattern_boundaries():
    result = Transcription("Long recording", 300, 120,
                           [DetectedPart("Piano", [DetectedNote(60, 127.75, 128.25, .9),
                                                    DetectedNote(62, 299, 299.9, .8)])], "single")
    project = transcribed_project(Project(), result)
    assert [p.bars for p in project.patterns[1:]] == [64, 64, 22]
    assert [(c.start_beat, c.length_beats) for c in project.rows[-1].clips] == [(0, 256), (256, 256), (512, 88)]
    assert project.patterns[1].notes[0].duration == .5
    assert project.patterns[2].notes[0].start == 0
    assert project.patterns[2].notes[0].duration == .5
    Project.from_dict(project.to_dict())


def test_empty_and_invalid_results_do_not_change_project():
    project = Project()
    before = project.to_dict()
    with pytest.raises(ValueError, match="No detected"):
        transcribed_project(project, Transcription("Empty", 1, 120, [], "single"))
    result = example_result()
    result.parts[0].notes.append(DetectedNote(60, 0, float("nan"), .5))
    with pytest.raises(ValueError, match="invalid"):
        transcribed_project(project, result)
    assert project.to_dict() == before


def pump_until(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    QApplication.processEvents()
    assert condition(), "Background operation did not complete"


def test_job_cancels_late_completion_without_success():
    entered, release = threading.Event(), threading.Event()

    def converter(source, **kwargs):
        entered.set()
        release.wait(3)
        return example_result()

    job = TranscriptionJob("unused", {}, convert=converter)
    success, failures, finished = [], [], []
    job.succeeded.connect(success.append)
    job.failed.connect(failures.append)
    job.finished.connect(lambda: finished.append(True))
    job.start()
    assert entered.wait(2)
    job.cancel()
    release.set()
    pump_until(lambda: bool(finished))
    assert not success and "cancelled" in failures[0]
    job.deleteLater()


@pytest.fixture
def window(tmp_path, monkeypatch):
    from mpclab.engine import Engine
    from mpclab.ui.main_window import MainWindow

    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = MainWindow(tmp_path, restore_session=False)
    yield window
    window._dirty = False
    window.close()


def test_real_dialog_converts_previews_adds_and_undoes(window, musical_audio):
    dialog = TranscriptionDialog(window)
    dialog.mode.setCurrentIndex(2)
    dialog.auto_tempo.setChecked(False)
    dialog.bpm.setValue(120)
    dialog.path.setText(str(musical_audio))
    before = window.project.to_dict()
    dialog.start_conversion()
    pump_until(lambda: dialog.job is None)
    assert dialog.result.note_count == 5
    assert dialog.preview.renderer().isValid()
    assert dialog.add.isEnabled()
    dialog.parts.item(0, 0).setText("Transcribed piano")
    dialog.add_parts()
    assert window.project.instruments[-1].name == "Transcribed piano"
    assert window.scoring_panel.scope.currentIndex() == 1
    assert window.studio.selected == 9
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.rows[-1].mute
    assert len(window.project.patterns[-1].notes) == 5
    dialog.deleteLater()


def test_project_replacement_during_conversion_blocks_insertion(window):
    dialog = TranscriptionDialog(window)
    dialog.source_project = window.project
    dialog.completed(example_result())
    window.project = Project(name="Different song")
    dialog.add_parts()
    assert window.project.name == "Different song"
    assert not window.project.instruments
    assert "project changed" in dialog.status.text()
    dialog.deleteLater()


def test_arranged_source_job_renders_snapshot_before_transcribing(tmp_path, monkeypatch):
    import mpclab.export

    project = Project(name="Arranged audio")
    rendered = []
    library = SimpleNamespace()

    def render(song, lib, destination, **kwargs):
        assert song is project and lib is library
        rendered.append(True)
        sf.write(destination, np.zeros(22050), 22050)

    def convert(path, **kwargs):
        assert rendered and Path(path).is_file()
        return example_result()

    monkeypatch.setattr(mpclab.export, "render_export", render)
    job = TranscriptionJob("", {}, song=project, library=library, convert=convert)
    results, finished = [], []
    job.succeeded.connect(results.append)
    job.finished.connect(lambda: finished.append(True))
    job.start()
    pump_until(lambda: bool(finished))
    assert results[0].title == "Arranged audio"
    job.deleteLater()
