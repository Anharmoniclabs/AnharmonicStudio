"""Arrange owns full-song mapping while source editors remain non-destructive."""

import numpy as np

from mpclab.music import Note
from mpclab.ui.arrangement_tools import (
    SONG_TEMPLATES,
    fit_song,
    map_full_song,
    place_sample_selection,
)
from test_arrangement_workflow import window as window


def test_full_song_map_creates_editable_sections_with_shared_hooks(window):
    source = window.project.pattern()
    source.name = "Main idea"
    source.steps = {0: {0: 0.9, 4: 0.7}, 1: {4: 0.8}}
    source.notes = [Note(60, 0.0, 0.5, 0.8)]
    before = window.project.to_dict()

    placed = map_full_song(window, "Trap · full song")

    assert len(placed) == len(SONG_TEMPLATES["Trap · full song"])
    assert placed[0].start_beat == 0
    assert placed[-1].start_beat + placed[-1].length_beats == 288
    assert window.project.song_end() == 288
    assert window.playlist.selected_clips == placed
    assert window.engine.mode == "song"
    assert window.btn_song.isChecked() and not window.btn_pattern.isChecked()

    names = {p.id: p.name for p in window.project.patterns}
    placed_names = [names[clip.ref] for clip in placed]
    assert placed_names[0].startswith("Intro ·")
    assert placed_names[1].startswith("Hook ·")
    assert placed_names[2].startswith("Verse 1 ·")
    assert placed_names[4].startswith("Verse 2 ·")
    assert placed[1].ref == placed[3].ref == placed[5].ref
    assert placed[2].ref != placed[4].ref

    # Section sources start as exact musical copies but remain independent where intended.
    hook = next(p for p in window.project.patterns if p.id == placed[1].ref)
    verse = next(p for p in window.project.patterns if p.id == placed[2].ref)
    assert hook.steps == source.steps and hook.notes == source.notes
    assert hook.steps[0] is not source.steps[0] and hook.notes[0] is not source.notes[0]
    verse.steps[0][0] = 0.1
    assert source.steps[0][0] == 0.9

    assert len(window._undo) == 1
    after = window.project.to_dict()
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.to_dict() == after


def test_song_map_appends_at_next_bar_without_overwriting_existing_arrangement(window):
    source = window.project.pattern()
    source.steps = {0: {0: 1.0}}
    first = window.append_pattern_to_arrangement()
    assert window.project.song_end() == source.length_beats

    placed = map_full_song(window, "Trap · compact")
    assert placed
    assert placed[0].start_beat == 8.0  # source pattern is two bars / eight beats
    assert first is None or first not in placed


def test_empty_pattern_map_is_a_noop(window):
    before = window.project.to_dict()
    assert map_full_song(window, "Trap · full song") == []
    assert window.project.to_dict() == before
    assert not window._undo


def test_invalid_template_is_a_noop(window):
    window.project.pattern().steps = {0: {0: 1.0}}
    before = window.project.to_dict()
    assert map_full_song(window, "does not exist") == []
    assert window.project.to_dict() == before
    assert not window._undo


def test_sample_selection_maps_exact_trim_to_arrange(window):
    audio = np.zeros((4800, 2), dtype=np.float32)
    audio[:, 0] = np.linspace(-0.1, 0.1, len(audio), dtype=np.float32)
    media = window.library.add_audio(audio, "Exact chop")
    window.current_clip = media.id
    window.wave.selection = lambda: (0.02, 0.075)
    window.set_mode("pattern")

    before = window.project.to_dict()
    clip = place_sample_selection(window)
    assert clip is not None
    assert clip.kind == "audio" and clip.ref == media.id
    assert clip.offset == 0.02
    assert abs(clip.source_length - 0.055) < 1e-12
    assert abs(clip.length_beats - 0.055 * window.project.bpm / 60.0) < 1e-12
    assert window.playlist.row_for_clip(clip).name == "AUDIO"
    assert window.engine.mode == "song"
    assert window.btn_song.isChecked() and not window.btn_pattern.isChecked()
    assert len(window._undo) == 1
    window.undo()
    assert window.project.to_dict() == before


def test_fit_song_changes_view_only(window):
    source = window.project.pattern()
    source.steps = {0: {0: 1.0}}
    map_full_song(window, "Trap · compact")
    before = window.project.to_dict()
    old_zoom = window.playlist.px_per_beat
    assert fit_song(window)
    assert window.project.to_dict() == before
    assert 4.0 <= window.playlist.px_per_beat <= 64.0
    assert window.playlist.px_per_beat != old_zoom or window.project.song_end() <= 16


def test_arrange_workspace_buttons_have_live_actions(window):
    window.studio.select(2)
    assert not window.studio.arrange_tools.isHidden()
    assert window.studio.arrange_pattern.text() == "ADD CURRENT PATTERN"

    window.project.pattern().steps = {0: {0: 1.0}}
    window.studio.song_template.setCurrentText("Trap · compact")
    window.studio.map_song.click()
    assert window.project.song_end() == 144
    assert window.studio.selected == 2
    assert window.engine.mode == "song"

    # MAP FULL SONG already fits the timeline. FIT SONG is intentionally
    # idempotent when the same viewport and song length are unchanged.
    before_project = window.project.to_dict()
    before_zoom = window.playlist.px_per_beat
    window.studio.fit_arrange.click()
    assert window.project.to_dict() == before_project
    assert 4.0 <= window.playlist.px_per_beat <= 64.0
    assert abs(window.playlist.px_per_beat - before_zoom) < 1e-12
