"""Trackpad packets preserve the pointed time and independent vertical scale."""

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from mpclab.ui.waveform import WaveformView
from mpclab.ui.playlist import HEAD_W, RULER_H
from tests.test_product_hardening_ui import window  # noqa: F401


def wheel(widget, pixel=(0, 0), angle=(0, 0), mods=Qt.NoModifier, pos=(250, 100)):
    event = QWheelEvent(
        QPointF(*pos),
        QPointF(*pos),
        QPoint(*pixel),
        QPoint(*angle),
        Qt.NoButton,
        mods,
        Qt.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_wave_trackpad_ignores_empty_packet_and_anchors_continuous_zoom():
    view = WaveformView()
    view.resize(1000, 300)
    view.set_clip(np.zeros((480000, 2)), np.zeros((100, 2)), 10, [])
    view.view_a, view.view_b = 0.25, 0.75
    anchor = view.x_to_time(250)
    wheel(view)
    assert (view.view_a, view.view_b) == (0.25, 0.75)
    wheel(view, pixel=(0, 2), mods=Qt.ControlModifier)
    assert 0.49 < view.view_b - view.view_a < 0.5
    assert view.x_to_time(250) == pytest.approx(anchor)
    span = view.view_b - view.view_a
    wheel(view, pixel=(20, 0))
    assert view.view_b - view.view_a == pytest.approx(span)
    wheel(view, pixel=(0, 20), mods=Qt.AltModifier)
    assert view.amp_zoom > 1
    assert view.view_b - view.view_a == pytest.approx(span)
    view.reset_view()
    assert (view.view_a, view.view_b, view.amp_zoom) == (0, 1, 1)


def test_long_sample_slider_reaches_one_frame(window):  # noqa: F811
    frames = 240 * 48000
    window.wave.set_clip(np.zeros((frames, 1), dtype=np.float32), np.zeros((100, 2)), 240, [])
    window.wave_zoom.setValue(1000)
    assert (window.wave.view_b - window.wave.view_a) * frames == pytest.approx(1)
    assert window._span_to_slider(window.wave.minimum_span()) == 1000


def test_arrangement_trackpad_zoom_keeps_pointed_beat_and_row(window):  # noqa: F811
    window.resize(1200, 800)
    window.show()
    window.studio.select(2)
    QApplication.processEvents()
    view = window.playlist
    x = HEAD_W + 200
    before = view.x_to_beat(x)
    wheel(view, pixel=(0, 25), mods=Qt.ControlModifier, pos=(x, 150))
    actual_x = x + window.song_scroll.horizontalScrollBar().value()
    assert view.x_to_beat(actual_x) == pytest.approx(before, abs=1 / view.px_per_beat)
    scale = view.px_per_beat
    y = RULER_H + 3.5 * view.row_height
    wheel(view, pixel=(0, 30), mods=Qt.AltModifier, pos=(actual_x, y))
    assert view.row_height > 54
    assert view.px_per_beat == scale
    assert view.row_at(RULER_H + 3.5 * view.row_height) == 3
    view.grab()
    view.reset_zoom()
    assert (view.row_height, view.px_per_beat) == (54, 26)


def test_native_pinch_zoom_changes_time_without_changing_height(window):  # noqa: F811
    from PySide6.QtGui import QNativeGestureEvent, QPointingDevice

    view = window.wave
    view.resize(1000, 300)
    view.set_clip(np.zeros((48000, 2)), np.zeros((100, 2)), 1, [])
    view.zoom_by(0.5, 0.5)
    before = view.x_to_time(250)
    for widget in (view, window.playlist):
        position = QPointF(250, 100)
        event = QNativeGestureEvent(
            Qt.ZoomNativeGesture,
            QPointingDevice.primaryPointingDevice(),
            2,
            position,
            position,
            position,
            0.1,
            QPointF(),
        )
        QApplication.sendEvent(widget, event)
        assert event.isAccepted()
    assert view.view_b - view.view_a < 0.5
    assert view.x_to_time(250) == pytest.approx(before)
    assert view.amp_zoom == 1
    assert window.playlist.px_per_beat > 26
    assert window.playlist.row_height == 54


def test_fit_long_arrangement_and_menu_remain_usable_narrow(window):  # noqa: F811
    window.resize(760, 800)
    window.show()
    window.studio.select(2)
    window.project.loop_end = 800
    QApplication.processEvents()
    window.playlist.fit_song()
    assert window.playlist.beat_to_x(800) < window.song_scroll.viewport().width()
    assert window.track_zoom_button.isVisible()
    assert window.track_zoom_button.menu() is not None
