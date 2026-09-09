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
