"""Arrangement gestures preserve phrase relationships and reversible edits."""

from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent

from mpclab.model import Clip, Project, Row
from mpclab.music import Note
from mpclab.ui.playlist import ROW_H, RULER_H, PlaylistView
from test_arrangement_workflow import window as window


@pytest.fixture
def playlist():
    project = Project()
    project.rows.extend([Row(), Row()])
    app = SimpleNamespace(project=project, snapshot=lambda: None)
    view = PlaylistView(app)
    view.tool = "select"
    view.snap = 1
    yield view
    view.close()


def pointer(view, kind, beat, row, modifiers=Qt.NoModifier):
    pos = QPointF(view.beat_to_x(beat), RULER_H + (row + 0.5) * ROW_H)
    button = Qt.NoButton if kind == QMouseEvent.MouseMove else Qt.LeftButton
    event = QMouseEvent(kind, pos, pos, button, Qt.LeftButton, modifiers)
    handler = {
        QMouseEvent.MouseButtonPress: view.mousePressEvent,
        QMouseEvent.MouseMove: view.mouseMoveEvent,
        QMouseEvent.MouseButtonRelease: view.mouseReleaseEvent,
    }[kind]
    handler(event)


def test_drag_keeps_selected_clips_and_their_time_and_lane_spacing(playlist):
    first = Clip(start_beat=4, length_beats=4)
    second = Clip(start_beat=8, length_beats=4)
    playlist.rows()[1].clips.append(first)
    playlist.rows()[3].clips.append(second)
    playlist.set_selection([first, second])
    pointer(playlist, QMouseEvent.MouseButtonPress, 5, 1)
    pointer(playlist, QMouseEvent.MouseMove, 7, 2)
    pointer(playlist, QMouseEvent.MouseButtonRelease, 7, 2)
    assert playlist.selected_clips == [first, second]
    assert (first.start_beat, second.start_beat) == (6, 10)
    assert playlist.row_for_clip(first) is playlist.rows()[2]
    assert playlist.row_for_clip(second) is playlist.rows()[4]


def test_group_drag_clamps_as_a_unit_at_song_and_lane_boundaries(playlist):
    first = Clip(start_beat=1, length_beats=4)
    second = Clip(start_beat=7, length_beats=4)
    playlist.rows()[0].clips.append(first)
    playlist.rows()[2].clips.append(second)
    playlist.set_selection([first, second])
    pointer(playlist, QMouseEvent.MouseButtonPress, 8, 2)
    pointer(playlist, QMouseEvent.MouseMove, 1, 0)
    assert (first.start_beat, second.start_beat) == (0, 6)
    assert playlist.row_for_clip(first) is playlist.rows()[0]
    assert playlist.row_for_clip(second) is playlist.rows()[2]
    # Moving back uses original positions rather than accumulating clamping error.
    pointer(playlist, QMouseEvent.MouseMove, 9, 3)
    pointer(playlist, QMouseEvent.MouseButtonRelease, 9, 3)
    assert (first.start_beat, second.start_beat) == (2, 8)
    assert playlist.row_for_clip(first) is playlist.rows()[1]
    assert playlist.row_for_clip(second) is playlist.rows()[3]


def test_ctrl_marquee_adds_to_existing_selection(playlist):
    first = Clip(start_beat=1, length_beats=2)
    second = Clip(start_beat=8, length_beats=2)
    playlist.rows()[0].clips.extend([first, second])
    playlist.set_selection([first])
    pointer(playlist, QMouseEvent.MouseButtonPress, 7, 0, Qt.ControlModifier)
    pointer(playlist, QMouseEvent.MouseMove, 11, 1, Qt.ControlModifier)
    pointer(playlist, QMouseEvent.MouseButtonRelease, 11, 1, Qt.ControlModifier)
    assert playlist.selected_clips == [first, second]


@pytest.mark.parametrize(
    "control,field,value,expected",
    [
        ("clip_gain", "gain", 65, 0.65),
        ("clip_crossfade", "loop_crossfade", 12.5, 0.0125),
        ("clip_track", "track", 3, 3),
    ],
)
def test_clip_inspector_edits_restore_exact_values_and_survive_reopen(
    window, control, field, value, expected
):
    media = window.library.add_audio(np.zeros((480, 2), dtype=np.float32), "Test take")
    clip = Clip(kind="audio", ref=media.id, track=1, gain=0.87654321, loop_crossfade=0.003456789)
    window.project.rows[0].clips.append(clip)
    window.playlist.select_clip(clip)
    before = window.project.to_dict()
    widget = getattr(window, control)
    if control == "clip_track":
        widget.setCurrentIndex(value)
    else:
        widget.setValue(value)
    after = window.project.to_dict()
    assert getattr(clip, field) == expected
    assert len(window._undo) == 1
    path = window.projects_dir / "clip-edit.json"
    assert window._save_project_to(path)
    assert window.load_project_path(path)
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.to_dict() == after


def test_selecting_clip_does_not_round_values_or_discard_redo(window):
    clip = Clip(kind="audio", gain=0.87654321, loop_crossfade=0.003456789)
    window.project.rows[0].clips.append(clip)
    window.snapshot()
    clip.gain = 0.5
    window.undo()
    before = window.project.to_dict()
    window.playlist.select_clip(window.project.rows[0].clips[0])
    assert window.project.to_dict() == before
    assert not window._undo and len(window._redo) == 1


def test_group_drag_undo_restores_all_clips(window):
    first = Clip(start_beat=4, length_beats=4)
    second = Clip(start_beat=8, length_beats=4)
    window.project.rows[0].clips.extend([first, second])
    before = window.project.to_dict()
    window.playlist.tool = "select"
    window.playlist.snap = 1
    window.playlist.set_selection([first, second])
    pointer(window.playlist, QMouseEvent.MouseButtonPress, 5, 0)
    pointer(window.playlist, QMouseEvent.MouseMove, 7, 1)
    pointer(window.playlist, QMouseEvent.MouseButtonRelease, 7, 1)
    after = window.project.to_dict()
    assert first.start_beat == 6 and second.start_beat == 10
    assert len(window._undo) == 1
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.to_dict() == after


def test_click_without_drag_preserves_redo_and_clean_project(window):
    clip = Clip(start_beat=4, length_beats=4)
    window.project.rows[0].clips.append(clip)
    window.snapshot()
    clip.start_beat = 8
    window.undo()
    window._set_dirty(False)
    before = window.project.to_dict()
    pointer(window.playlist, QMouseEvent.MouseButtonPress, 5, 0)
    pointer(window.playlist, QMouseEvent.MouseButtonRelease, 5, 0)
    assert window.project.to_dict() == before
    assert not window._dirty and not window._undo and len(window._redo) == 1


def test_make_unique_preserves_playback_placement_and_saved_undo(window):
    source = window.project.pattern()
    source.steps = {2: {1: 0.57, 7: 0.92}}
    source.notes = [Note(62, 0.125, 0.375, 0.67), Note(69, 2.3, 0.8, 0.49)]
    first = Clip(ref=source.id, length_beats=8)
    second = Clip(ref=source.id, start_beat=8, length_beats=8, gain=0.71)
    window.project.rows[0].clips.extend([first, second])
    window.playlist.select_clip(second)
    assert "shared by 2 clips" in window.playlist_clip_name.text()
    window.engine.mode = "song"
    events_before = window.engine._collect(0, 16)
    before = window.project.to_dict()
    window.btn_clip_unique.click()
    pattern = window.project.pattern()
    assert first.ref == source.id and second.ref == pattern.id != source.id
    assert (second.start_beat, second.length_beats, second.gain) == (8, 8, 0.71)
    assert pattern.steps == source.steps and pattern.notes == source.notes
    assert pattern.steps[2] is not source.steps[2] and pattern.notes[0] is not source.notes[0]
    assert window.engine._collect(0, 16) == events_before
    assert "shared by" not in window.playlist_clip_name.text()
    assert window.place_box.currentData() == ("pattern", pattern.id)
    after = window.project.to_dict()
    assert len(window._undo) == 1
    path = window.projects_dir / "variation.json"
    assert window._save_project_to(path)
    assert window.load_project_path(path)
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.to_dict() == after
    unique = window.project.pattern()
    original = next(p for p in window.project.patterns if p.id == source.id)
    unique.steps[2][1] = 0.1
    unique.notes[0].pitch = 72
    assert original.steps[2][1] == 0.57 and original.notes[0].pitch == 62


def test_make_unique_ignores_audio_missing_and_stale_clips(window):
    audio = Clip(kind="audio")
    missing = Clip(ref="missing-pattern")
    window.project.rows[0].clips.extend([audio, missing])
    before = window.project.to_dict()
    assert window.make_pattern_unique(audio) is None
    assert window.make_pattern_unique(missing) is None
    assert window.make_pattern_unique(Clip(ref=window.project.pattern().id)) is None
    assert window.make_pattern_unique() is None
    assert window.project.to_dict() == before and not window._undo
