"""Cuts and drag destinations preserve source audio, timing, and history."""

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest

from mpclab.engine import Engine
from mpclab.model import Clip, Project
from mpclab.ui import main_window, waveform
from mpclab.ui.playlist import ROW_H, RULER_H
from mpclab.ui.sample_drag import RANGE_MIME, sample_range
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    instance = main_window.MainWindow(tmp_path, restore_session=False)
    audio = np.linspace(-0.25, 0.25, 96_000, dtype=np.float32)
    source = instance.library.add_audio(np.column_stack((audio, audio)), "Test sample")
    instance.load_clip_into_editor(source.id)
    instance.wave.snap_mode = "off"
    instance.wave.resize(1000, 300)
    yield instance
    instance._dirty = False
    instance.close()


def mime_for(window, start=0.25, end=0.3125):
    mime = QMimeData()
    mime.setData(RANGE_MIME, f"{window.current_clip}|{start}|{end}".encode())
    return mime


def test_manual_cut_includes_both_sides_and_undo_restores_markers(window):
    before = window.project.to_dict()
    window.cut_sample_button.click()
    QTest.mouseClick(window.wave, Qt.LeftButton, pos=QPoint(500, 100))
    assert window.wave.all_slices() == [(0, 1), (1, 2)]
    assert len(window._slice_buttons) == 2
    assert len(window._undo) == 1
    window.wave.add_marker(1)  # duplicate and endpoint cuts are no-ops
    window.wave.add_marker(2)
    assert len(window._undo) == 1
    window.undo()
    assert window.project.to_dict() == before
    assert window.wave.markers == []
    window.redo()
    assert window.wave.markers == [0, 1]


def test_waveform_range_shortcuts_send_to_arrange_or_current_pad(window):
    """The range handoff stays on the waveform, away from number-entry fields."""
    window.wave.set_selection(0.25, 0.5, emit=False)

    window.show()
    window.show_tab(window.TAB_CHOP)
    window.wave.setFocus()
    QTest.keyClick(window.wave, Qt.Key_Return, Qt.ControlModifier)
    QTest.keyClick(window.wave, Qt.Key_Return, Qt.ControlModifier | Qt.ShiftModifier)

    assert window.playlist.selected_clip is not None
    pad = window.project.pads[window.pads.selected]
    assert (pad.sample_id, pad.start, pad.end) == (window.current_clip, 0.25, 0.5)


def test_slice_button_drag_carries_the_auditioned_slice(window, monkeypatch):
    window.wave.add_marker(0.25)
    window.wave.add_marker(0.3125)
    captured = []

    class FakeDrag:
        def __init__(self, source):
            pass

        def setMimeData(self, mime):
            captured.append(sample_range(mime, window.library))

        def setPixmap(self, pixmap):
            pass

        def exec(self, action):
            return Qt.CopyAction

    monkeypatch.setattr(waveform, "QDrag", FakeDrag)
    button = window._slice_buttons[1]
    QTest.mousePress(button, Qt.LeftButton, pos=QPoint(5, 5))
    move = QMouseEvent(
        QMouseEvent.MouseMove,
        QPointF(45, 5),
        QPointF(45, 5),
        Qt.NoButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    button.mouseMoveEvent(move)
    assert captured == [(window.current_clip, 0.25, 0.3125)]
    assert not button.isDown()


def test_drop_on_lane_keeps_short_range_and_exact_audio_and_can_undo(window):
    window.project.bpm = 120
    before = window.project.to_dict()
    point = QPointF(window.playlist.beat_to_x(8), RULER_H + ROW_H * 2.5)
    mime = mime_for(window)
    enter = QDragEnterEvent(point.toPoint(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.playlist.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.playlist.dropEvent(drop)
    assert drop.isAccepted()
    placed = window.playlist.selected_clip
    assert window.project.rows[2].clips == [placed]
    assert placed.start_beat == 8
    assert placed.offset == 0.25
    assert placed.source_length == 0.0625
    assert placed.length_beats == 0.125
    assert not placed.loop
    data, first, last = window.engine._audio_clip_source(placed)
    assert (first, last) == (12_000, 15_000)
    np.testing.assert_array_equal(data[first:last], window.library.audio(placed.ref)[12000:15000])
    after = window.project.to_dict()
    assert Project.from_dict(after).to_dict() == after
    assert len(window._undo) == 1
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.to_dict() == after


@pytest.mark.parametrize("studio", [False, True])
def test_arrange_navigation_accepts_hover_and_direct_drop(window, studio):
    if studio:
        window.studio.select(0)
        target = window.studio.buttons[2]
        point = QPoint(10, 10)
    else:
        window.show_tab(0)
        target = window.tabs.tabBar()
        point = target.tabRect(2).center()
    mime = mime_for(window)
    enter = QDragEnterEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window._arrange_drop_filter.eventFilter(target, enter)
    assert enter.isAccepted()
    QTest.qWait(400)
    assert window.studio.selected == 2
    assert window.tabs.currentIndex() == 8
    assert not any(row.clips for row in window.project.rows)
    drop = QDropEvent(QPointF(point), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window._arrange_drop_filter.eventFilter(target, drop)
    assert drop.isAccepted()
    assert window.playlist.selected_clip.source_length == 0.0625
    assert len(window._undo) == 1


def test_send_button_appends_to_selected_lane_without_overwriting(window):
    previous = Clip(start_beat=1, length_beats=4.5)
    window.project.rows[3].clips.append(previous)
    window.playlist.select_clip(previous)
    window.wave.set_selection(0.25, 0.3125)
    window.send_sample_button.click()
    placed = window.playlist.selected_clip
    assert window.project.rows[3].clips == [previous, placed]
    assert placed.start_beat == 8
    assert placed.offset == 0.25
    assert placed.source_length == 0.0625


def test_dragging_an_audio_clip_right_edge_time_stretches_without_trimming(window):
    clip = Clip(
        kind="audio",
        ref=window.current_clip,
        start_beat=0.0,
        length_beats=2.0,
        offset=0.25,
        source_length=0.5,
    )
    window.project.rows[0].clips.append(clip)
    window.playlist.snap = 1.0
    window.show()
    QTest.qWait(10)
    edge = QPoint(int(window.playlist.beat_to_x(2.0)), RULER_H + ROW_H // 2)
    QTest.mousePress(window.playlist, Qt.LeftButton, pos=edge)
    assert window.playlist._drag["mode"] == "stretch"
    target = QPointF(window.playlist.beat_to_x(8.0), edge.y())
    move = QMouseEvent(
        QMouseEvent.MouseMove,
        target,
        target,
        Qt.NoButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    window.playlist.mouseMoveEvent(move)
    QTest.mouseRelease(window.playlist, Qt.LeftButton, pos=target.toPoint())

    assert clip.length_beats == 8.0
    assert (clip.offset, clip.source_length) == (0.25, 0.5)
    assert "stretched" in window.status.currentMessage()


@pytest.mark.parametrize("reverse", [False, True])
def test_shift_right_edge_trims_audio_without_changing_rate(window, reverse):
    clip = Clip(
        kind="audio",
        ref=window.current_clip,
        length_beats=8,
        offset=0.25,
        source_length=0.5,
        reverse=reverse,
    )
    window.project.rows[0].clips.append(clip)
    window.playlist.snap = 1.0
    y = RULER_H + ROW_H // 2
    edge = QPoint(int(window.playlist.beat_to_x(8)), y)
    QTest.mousePress(window.playlist, Qt.LeftButton, Qt.ShiftModifier, edge)
    assert window.playlist._drag["mode"] == "trim-right"
    target = QPointF(window.playlist.beat_to_x(4), y)
    window.playlist.mouseMoveEvent(
        QMouseEvent(
            QMouseEvent.MouseMove, target, target, Qt.NoButton, Qt.LeftButton, Qt.ShiftModifier
        )
    )
    QTest.mouseRelease(window.playlist, Qt.LeftButton, Qt.ShiftModifier, target.toPoint())
    assert clip.length_beats == 4
    assert clip.source_length == pytest.approx(0.25)
    assert clip.offset == pytest.approx(0.5 if reverse else 0.25)


def test_narrow_audio_clip_uses_nearest_endpoint_for_trim_or_stretch(window):
    """Overlapping edge targets still let either end of a tiny clip be used."""
    clip = Clip(
        kind="audio",
        ref=window.current_clip,
        start_beat=0.0,
        length_beats=0.25,  # 6.5 px at the default Arrange zoom
    )
    window.project.rows[0].clips.append(clip)
    window.show()
    QTest.qWait(10)
    y = RULER_H + ROW_H // 2

    left = QPoint(int(window.playlist.beat_to_x(clip.start_beat)), y)
    QTest.mousePress(window.playlist, Qt.LeftButton, pos=left)
    assert window.playlist._drag["mode"] == "trim-left"
    QTest.mouseRelease(window.playlist, Qt.LeftButton, pos=left)

    right = QPoint(int(window.playlist.beat_to_x(clip.start_beat + clip.length_beats)), y)
    QTest.mousePress(window.playlist, Qt.LeftButton, pos=right)
    assert window.playlist._drag["mode"] == "stretch"
    QTest.mouseRelease(window.playlist, Qt.LeftButton, pos=right)


def test_splitting_a_stretched_audio_clip_preserves_source_proportions(window):
    clip = Clip(
        kind="audio",
        ref=window.current_clip,
        start_beat=0.0,
        length_beats=8.0,
        offset=0.25,
        source_length=0.5,
    )
    window.project.rows[0].clips.append(clip)

    window.playlist.split_clip(clip, 4.0)

    right = window.playlist.selected_clip
    assert clip.length_beats == 4.0
    assert clip.source_length == pytest.approx(0.25)
    assert right.start_beat == 4.0
    assert right.offset == pytest.approx(0.5)
    assert right.source_length == pytest.approx(0.25)


def test_left_trim_of_stretched_audio_clip_preserves_warp_ratio(window):
    clip = Clip(
        kind="audio",
        ref=window.current_clip,
        start_beat=0.0,
        length_beats=8.0,
        offset=0.25,
        source_length=0.5,
    )
    window.project.rows[0].clips.append(clip)
    window.playlist.snap = 1.0
    window.show()
    QTest.qWait(10)
    edge = QPoint(int(window.playlist.beat_to_x(0.0)), RULER_H + ROW_H // 2)
    QTest.mousePress(window.playlist, Qt.LeftButton, pos=edge)
    assert window.playlist._drag["mode"] == "trim-left"
    target = QPointF(window.playlist.beat_to_x(2.0), edge.y())
    move = QMouseEvent(
        QMouseEvent.MouseMove, target, target, Qt.NoButton, Qt.LeftButton, Qt.NoModifier
    )
    window.playlist.mouseMoveEvent(move)
    QTest.mouseRelease(window.playlist, Qt.LeftButton, pos=target.toPoint())

    assert clip.start_beat == 2.0
    assert clip.length_beats == 6.0
    assert clip.offset == pytest.approx(0.375)
    assert clip.source_length == pytest.approx(0.375)


def test_left_edge_shortens_a_pattern_clip_without_changing_its_end(window):
    """Patterns use the same two-ended Arrange trim contract as audio."""
    clip = Clip(kind="pattern", ref=window.project.pattern().id, start_beat=1.0, length_beats=4.0)
    window.project.rows[0].clips.append(clip)
    window.playlist.snap = 1.0
    window.show()
    QTest.qWait(10)
    y = RULER_H + ROW_H // 2
    left = QPoint(int(window.playlist.beat_to_x(1.0)), y)
    QTest.mousePress(window.playlist, Qt.LeftButton, pos=left)
    assert window.playlist._drag["mode"] == "trim-left"
    target = QPointF(window.playlist.beat_to_x(2.0), y)
    window.playlist.mouseMoveEvent(
        QMouseEvent(
            QMouseEvent.MouseMove, target, target, Qt.NoButton, Qt.LeftButton, Qt.NoModifier
        )
    )
    QTest.mouseRelease(window.playlist, Qt.LeftButton, pos=target.toPoint())

    assert (clip.start_beat, clip.length_beats) == (2.0, 3.0)


@pytest.mark.parametrize("start,end", [(-1, 1), (1, 1), (1, 3), (0, "nan"), (0, "inf")])
def test_invalid_drop_does_not_change_project_or_history(window, start, end):
    before = window.project.to_dict()
    point = QPointF(window.playlist.beat_to_x(4), RULER_H + ROW_H / 2)
    mime = mime_for(window, start, end)
    drop = QDropEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.playlist.dropEvent(drop)
    assert not drop.isAccepted()
    assert window.project.to_dict() == before
    assert not window._undo
