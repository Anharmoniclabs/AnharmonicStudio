"""Timeline picking, writing, and clipboard edits use the chosen sound."""

from dataclasses import asdict

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent, QKeyEvent, QMouseEvent

from mpclab.model import Clip, Project
from mpclab.ui.playlist import ROW_H, RULER_H
from test_arrangement_workflow import window as window
from test_arrangement_editing import pointer


def source_clip(window, **changes):
    meta = window.library.add_audio(
        np.linspace(-0.25, 0.25, 96000, dtype=np.float32), "Picked bass"
    )
    clip = Clip(
        kind="audio",
        ref=meta.id,
        start_beat=0,
        length_beats=1,
        offset=0.25,
        source_length=0.5,
        reverse=True,
        gain=0.65,
        track=5,
    )
    for name, value in changes.items():
        setattr(clip, name, value)
    window.project.rows[0].clips.append(clip)
    window.playlist.select_clip(clip)
    return clip


def settings(clip):
    return {k: v for k, v in asdict(clip).items() if k not in ("id", "start_beat")}


def gesture(view, start, end=None, row=1):
    pointer(view, QMouseEvent.MouseButtonPress, start, row)
    if end is not None:
        pointer(view, QMouseEvent.MouseMove, end, row)
    pointer(view, QMouseEvent.MouseButtonRelease, end if end is not None else start, row)


def test_pick_then_draw_preserves_the_selected_sample_settings_and_history(window):
    source = source_clip(window)
    assert window.place_box.currentData() is None
    assert window.playlist.place == ("audio", source.ref)
    window._refresh_place_box()
    assert [window.place_box.itemData(i) for i in range(window.place_box.count())] == [
        ("pattern", pattern.id) for pattern in window.project.patterns
    ]
    assert window.place_box.currentData() is None
    assert window.playlist.place == ("audio", source.ref)
    before = window.project.to_dict()
    window.set_playlist_tool("draw")
    gesture(window.playlist, 4)
    copy = window.playlist.selected_clip
    assert settings(copy) == settings(source)
    assert copy.id != source.id and copy.start_beat == 4
    assert len(window._undo) == 1
    after = window.project.to_dict()
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.to_dict() == after


def test_fast_paint_fills_intermediate_positions_as_one_undo(window):
    source = source_clip(window)
    window.playlist.snap = 1
    window.set_playlist_tool("paint")
    before = window.project.to_dict()
    gesture(window.playlist, 4, 8)
    copies = window.project.rows[1].clips
    assert [clip.start_beat for clip in copies] == [4, 5, 6, 7, 8]
    assert all(settings(clip) == settings(source) for clip in copies)
    assert len(window._undo) == 1
    window.undo()
    assert window.project.to_dict() == before


def test_pattern_dropdown_explicitly_replaces_sample_brush(window):
    source_clip(window)
    window._choose_pattern_to_place(window.project.pattern().id)
    gesture(window.playlist, 4)
    assert window.playlist.selected_clip.kind == "pattern"


def test_browser_drop_selects_sample_for_subsequent_drawing(window):
    clip = source_clip(window)
    mime = QMimeData()
    mime.setData("application/x-mpclab-clip", clip.ref.encode())
    point = QPointF(window.playlist.beat_to_x(8), RULER_H + ROW_H * 2.5)
    drop = QDropEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.playlist.dropEvent(drop)
    assert drop.isAccepted()
    placed = window.playlist.selected_clip
    assert placed in window.project.rows[2].clips
    assert window.place_box.currentData() is None
    assert window.playlist.place == ("audio", clip.ref)
    gesture(window.playlist, 12)
    assert settings(window.playlist.selected_clip) == settings(placed)


def test_copy_cut_paste_preserves_group_spacing_and_is_reversible(window):
    first = source_clip(window)
    second = Clip(ref=window.project.pattern().id, start_beat=3, length_beats=4)
    window.project.rows[2].clips.append(second)
    window.playlist.set_selection([first, second])
    before = window.project.to_dict()
    window.playlist.copy_selected()
    assert not window._undo
    copies = window.playlist.paste_clips(8, 3)
    assert [c.start_beat for c in copies] == [8, 11]
    assert copies[0] in window.project.rows[3].clips
    assert copies[1] in window.project.rows[5].clips
    assert settings(copies[0]) == settings(first)
    window.undo()
    assert window.project.to_dict() == before
    window.playlist.set_selection(
        [window.project.rows[0].clips[0], window.project.rows[2].clips[0]]
    )
    window.playlist.copy_selected(cut=True)
    assert not window.project.rows[0].clips and not window.project.rows[2].clips
    window.undo()
    assert window.project.to_dict() == before


def test_empty_paint_and_track_click_preserve_redo(window):
    source_clip(window)
    window.snapshot()
    window.project.rows[0].clips.clear()
    window.undo()
    window._set_dirty(False)
    window.playlist.place = None
    window.set_playlist_tool("paint")
    gesture(window.playlist, 4)
    assert not window._undo and len(window._redo) == 1 and not window._dirty
    pos = QPointF(20, RULER_H + ROW_H / 2)
    for kind in (QMouseEvent.MouseButtonPress, QMouseEvent.MouseButtonRelease):
        event = QMouseEvent(kind, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        (
            window.playlist.mousePressEvent
            if kind == QMouseEvent.MouseButtonPress
            else window.playlist.mouseReleaseEvent
        )(event)
    assert not window._undo and len(window._redo) == 1 and not window._dirty


def test_write_notes_preserves_source_and_reuses_instrument_and_saves(window):
    clip = source_clip(window)
    before = window.project.to_dict()
    window.btn_clip_notes.click()
    slot = window.piano_roll.target_pad
    assert slot is not None
    pad = window.project.pads[slot]
    assert (pad.sample_id, pad.start, pad.end, pad.reverse, pad.gain, pad.track) == (
        clip.ref,
        0.25,
        0.75,
        True,
        0.65,
        5,
    )
    assert window.studio.selected == window.TAB_PIANO
    assert window.sample_workflow.from_arrangement(clip) == slot
    assert len(window._undo) == 1
    window.piano_roll.add_chord()
    assert {note.pad for note in window.project.pattern().notes} == {slot}
    saved = Project.from_dict(window.project.to_dict())
    assert saved.pads[slot] == pad
    assert saved.pattern().notes == window.project.pattern().notes
    window.piano_roll.arrange_button.click()
    placed = window.playlist.selected_clip
    assert placed.kind == "pattern" and placed.ref == window.project.pattern().id
    assert window.studio.selected == window.TAB_PLAYLIST
    window.open_pattern_clip(placed)
    assert window.piano_roll.target_pad == slot
    window.undo()
    window.undo()
    window.undo()
    assert window.project.to_dict() == before


@pytest.mark.parametrize("tool", ["slice", "mute", "erase"])
def test_edit_tools_change_sample_and_undo(window, tool):
    clip = source_clip(window, length_beats=4, reverse=False)
    before = window.project.to_dict()
    window.playlist.snap = 1
    window.playlist_tool_buttons[tool].click()
    gesture(window.playlist, 1, row=0)
    if tool == "slice":
        assert len(window.project.rows[0].clips) == 2
        assert clip.length_beats == 1
    elif tool == "mute":
        assert clip.mute
    else:
        assert not window.project.rows[0].clips
    assert len(window._undo) == 1
    window.undo()
    assert window.project.to_dict() == before


def test_clipboard_keys_and_split_shortcut(window):
    source_clip(window, length_beats=8)
    view = window.playlist
    window.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_C, Qt.ControlModifier))
    assert len(view.clipboard) == 1
    window.engine.beat = 8
    window.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_V, Qt.ControlModifier))
    assert view.selected_clip.start_beat == 8
    window.engine.beat = 10
    window.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_S, Qt.ShiftModifier))
    assert view.selected_clip.start_beat == 10


def test_nudge_selection_preserves_spacing_and_clamps_at_track_boundary(window):
    first = source_clip(window)
    second = Clip(ref=window.project.pattern().id, start_beat=3, length_beats=4)
    window.project.rows[2].clips.append(second)
    window.playlist.set_selection([first, second])
    before = window.project.to_dict()
    window.playlist.nudge_selected(beats=-1, rows=-1)
    assert not window._undo
    window.playlist.nudge_selected(beats=1, rows=1)
    assert (first.start_beat, second.start_beat) == (1, 4)
    assert first in window.project.rows[1].clips and second in window.project.rows[3].clips
    assert len(window._undo) == 1
    window.undo()
    assert window.project.to_dict() == before


@pytest.mark.parametrize("reverse,expected", [(False, (0.25, 0.5)), (True, (0.5, 0.75))])
def test_write_notes_uses_audible_side_of_shortened_clip(window, reverse, expected):
    window.project.bpm = 120
    clip = source_clip(window, length_beats=0.5, reverse=reverse)
    slot = window.sample_workflow.from_arrangement(clip)
    pad = window.project.pads[slot]
    assert (pad.start, pad.end) == expected


def test_unreadable_reversed_sample_does_not_create_instrument_or_history(window, monkeypatch):
    clip = source_clip(window)
    before = window.project.to_dict()

    def unavailable(_):
        raise OSError("Sample unavailable")

    monkeypatch.setattr(window.library, "reversed_audio", unavailable)
    assert window.sample_workflow.from_arrangement(clip) is None
    assert window.project.to_dict() == before and not window._undo
    assert "Could not load sound" in window.status.currentMessage()
