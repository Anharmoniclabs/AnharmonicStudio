"""A beat reaches the timeline intact, and its editor stays one gesture away."""

from copy import deepcopy

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.engine import Engine
from mpclab.export import render_export
from mpclab.model import Clip, Pattern, Project
from mpclab.music import Note
from mpclab.ui import main_window
from mpclab.ui.playlist import ROW_H, RULER_H
from mpclab.workflow import pattern_arrangement_target
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    instance = main_window.MainWindow(tmp_path, restore_session=False)
    yield instance
    instance._dirty = False
    instance.close()


def test_target_respects_selected_lane_and_all_off_grid_tails():
    project = Project()
    selected = Clip(ref="other", start_beat=0, length_beats=4)
    tail = Clip(kind="audio", ref="take", start_beat=7.1, length_beats=1.3)
    project.rows[2].clips = [tail, selected]
    before = project.to_dict()

    assert pattern_arrangement_target(project, project.pattern(), selected, 4) == (2, 12)
    assert pattern_arrangement_target(project, project.pattern(), selected, 0.25) == (2, 8.5)
    assert pattern_arrangement_target(project, project.pattern(), selected, 0) == (2, 8.4)
    assert project.to_dict() == before


def test_target_continues_current_pattern_then_uses_free_or_new_lane():
    project = Project()
    project.rows[0].clips = [Clip(kind="audio", ref="take")]
    project.rows[2].clips = [Clip(ref=project.pattern().id, length_beats=8)]
    assert pattern_arrangement_target(project, project.pattern()) == (2, 8)

    new_pattern = Pattern(name="New melody")
    assert pattern_arrangement_target(project, new_pattern) == (1, 0)
    for row in project.rows:
        if not row.clips:
            row.clips = [Clip(kind="audio", ref="other take")]
    assert pattern_arrangement_target(project, new_pattern) == (len(project.rows), 0)
    project.rows.clear()
    assert pattern_arrangement_target(project, new_pattern) == (0, 0)


def test_stale_selection_does_not_choose_an_unrelated_lane():
    project = Project()
    project.rows[0].clips = [Clip(kind="audio", ref="take")]
    assert pattern_arrangement_target(project, project.pattern(), Clip(ref="old")) == (1, 0)


def test_arrange_button_places_current_pattern_and_repeats_without_replacing(window):
    pattern = window.project.pattern()
    pattern.steps = {0: {0: 0.8, 7: 0.45}}
    old_pattern = Pattern(name="Unrelated placement choice")
    window.project.patterns.append(old_pattern)
    window._refresh_place_box()
    window.place_box.setCurrentIndex(1)
    before_patterns = deepcopy(window.project.to_dict()["patterns"])

    window.studio.arrange_pattern.click()
    first = window.playlist.selected_clip
    assert first.ref == pattern.id
    assert first.start_beat == 0
    assert first.length_beats == pattern.length_beats
    assert window.studio.enabled
    assert window.studio.selected == window.TAB_PLAYLIST
    assert window.place_box.currentData() == ("pattern", pattern.id)

    second = window.append_pattern_to_arrangement()
    assert second.ref == pattern.id
    assert second.id != first.id
    assert second.start_beat == pattern.length_beats
    assert window.project.rows[0].clips == [first, second]
    assert window.project.to_dict()["patterns"] == before_patterns
    assert len(window._undo) == 2


def test_empty_pattern_does_not_create_clip_or_history(window):
    before = window.project.to_dict()
    assert window.append_pattern_to_arrangement() is None
    assert window.project.to_dict() == before
    assert not window._undo
    assert "empty" in window.status.currentMessage()


def test_arrange_preserves_session_transport_and_round_trips_undo(window, tmp_path):
    pattern = window.project.pattern()
    pattern.notes = [Note(60, 0.125, 0.375, 0.63)]
    media = window.library.add_audio(np.zeros(480, dtype=np.float32), "Dry take")
    selected = Clip(kind="audio", ref=media.id, start_beat=1.25, length_beats=4.1)
    window.project.rows[3].clips = [selected]
    window.playlist.select_clip(selected)
    window.project.loop_start, window.project.loop_end = 20, 28
    window.project.loop_enabled = True
    window.engine.mode = "pattern"
    window.engine.playing = True
    window.engine.recording = True
    window.engine.beat = 2.5
    before = window.project.to_dict()

    clip = window.append_pattern_to_arrangement()

    assert clip.start_beat == 8
    assert window.project.rows[3].clips == [selected, clip]
    assert window.project.loop_start == 20 and window.project.loop_end == 28
    assert window.project.loop_enabled
    assert window.engine.mode == "pattern" and window.engine.playing and window.engine.recording
    assert window.engine.beat == 2.5
    after = window.project.to_dict()
    path = tmp_path / "arranged.json"
    window.project.save(path)
    assert Project.load(path).to_dict() == after

    window.undo()
    assert window.project.to_dict() == before
    assert window.playlist.selected_clip is None
    window.redo()
    assert window.project.to_dict() == after


def test_full_arrangement_adds_one_lane_and_undo_removes_it(window):
    window.project.pattern().steps = {1: {0: 0.8}}
    media = window.library.add_audio(np.zeros(480, dtype=np.float32), "Untouched take")
    for row in window.project.rows:
        row.clips = [Clip(kind="audio", ref=media.id, start_beat=4.5, length_beats=3.5)]
    before = window.project.to_dict()
    rows = len(window.project.rows)
    placed = window.append_pattern_to_arrangement()
    assert len(window.project.rows) == rows + 1
    assert window.project.rows[-1].clips == [placed]
    assert placed.start_beat == 0
    window.undo()
    assert window.project.to_dict() == before


@pytest.mark.parametrize("melodic", [False, True])
@pytest.mark.parametrize("studio", [False, True])
def test_double_click_opens_the_clips_actual_pattern_editor(window, melodic, studio):
    original = window.project.pattern()
    original.steps = {0: {0: 0.8}}
    if melodic:
        original.notes = [Note(60, 0.125, 0.375, 0.63)]
    clip = window.append_pattern_to_arrangement()
    other = Pattern(name="Other pattern")
    window.project.patterns.append(other)
    window.project.current_pattern = other.id
    window._sync_pattern_controls()
    if not studio:
        window.show_tab(window.TAB_PLAYLIST)
    before = deepcopy(original)
    undo_count = len(window._undo)
    window.show()
    QApplication.processEvents()

    point = QPoint(int(window.playlist.beat_to_x(2)), int(RULER_H + ROW_H / 2))
    QTest.mouseDClick(window.playlist, Qt.LeftButton, pos=point)

    assert window.project.current_pattern == clip.ref == original.id
    expected_tab = window.TAB_PIANO if melodic else window.TAB_SEQ
    assert window.studio.selected == expected_tab
    assert window.tabs.currentIndex() == 8
    assert window.studio.enabled
    assert window.project.pattern() == before
    assert len(window._undo) == undo_count


def test_appending_reveals_a_late_clip_on_a_low_lane(window):
    pattern = window.project.pattern()
    pattern.steps = {0: {0: 0.8}}
    selected = Clip(ref=pattern.id, start_beat=300, length_beats=8)
    window.project.rows[-1].clips = [selected]
    window.playlist.select_clip(selected)
    window.resize(1000, 700)
    window.show()
    QApplication.processEvents()

    placed = window.append_pattern_to_arrangement()
    QApplication.processEvents()

    point = window.playlist.mapTo(
        window.song_scroll.viewport(),
        QPoint(
            int(window.playlist.beat_to_x(placed.start_beat) + 20),
            int(RULER_H + (len(window.project.rows) - 0.5) * ROW_H),
        ),
    )
    assert window.song_scroll.viewport().rect().contains(point)


def test_arranged_notes_keep_timing_velocity_gate_and_export_after_reopen(window, tmp_path):
    pattern = window.project.pattern()
    pattern.bars = 1
    window.project.bpm = 240
    window.project.synth.noise = 0
    pattern.notes = [Note(60, 0.125, 0.375, 0.63), Note(67, 3.15, 0.6, 0.44)]
    window.append_pattern_to_arrangement()
    window.append_pattern_to_arrangement()
    window.engine.mode = "song"
    events, _ = window.engine._collect(0, pattern.length_beats * 2)
    expected = [
        (base + note.start, -note.pitch - 1, note.velocity, note.duration)
        for base in (0, pattern.length_beats)
        for note in pattern.notes
    ]
    assert [event[:4] for event in events] == pytest.approx(expected)
    path = tmp_path / "melody.json"
    window.project.save(path)
    restored = Project.load(path)
    destination = tmp_path / "arranged.wav"
    duration = render_export(restored, window.library, destination, tail=0, subtype="FLOAT")
    audio, rate = sf.read(destination)
    assert duration == 2
    assert rate == 48000
    assert np.isfinite(audio).all()
    assert np.max(np.abs(audio[: int(0.125 * 0.25 * rate)])) == 0
    assert np.max(np.abs(audio[:rate])) > 0.001
    assert np.max(np.abs(audio[rate:])) > 0.001
    assert window.project.to_dict() == restored.to_dict()


def test_search_shortcut_reveals_browser_and_preserves_editor(window):
    window.studio.select(window.TAB_PIANO)
    window.browser.search.setText("old search")
    window.browser_frame.hide()
    before = window.project.to_dict()
    window.show()
    QApplication.processEvents()
    window.piano_roll.canvas.setFocus()
    QTest.keyClick(window.piano_roll.canvas, Qt.Key_F, Qt.ControlModifier)
    QApplication.processEvents()

    assert not window.browser_frame.isHidden()
    assert window.browser.search.hasFocus()
    assert window.browser.search.selectedText() == "old search"
    assert window.studio.selected == window.TAB_PIANO
    assert window.studio.enabled
    assert window.project.to_dict() == before
