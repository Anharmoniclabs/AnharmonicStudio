"""Waveform display with slice markers — the CHOP view's main surface.

Laid out like an audio editor rather than a picture of a file: a ruler across
the top that scrubs, the wave in the middle, and a range strip along the bottom
whose ends trim. Everything the mouse can do has a key as well, because moving
a cut by one millisecond with a mouse is nobody's idea of a good time.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QRectF, Signal, QPointF, QMimeData, QPoint
from PySide6.QtGui import (
    QPainter,
    QPen,
    QBrush,
    QColor,
    QFont,
    QPolygonF,
    QDrag,
    QPixmap,
    QLinearGradient,
)
from PySide6.QtWidgets import QWidget

from .theme import q, hit_color
from .sample_drag import RANGE_MIME

RULER_H = 18.0  # scrub strip along the top
BAND_H = 22.0  # draggable range strip along the bottom


def draw_peaks(
    p: QPainter,
    peaks: np.ndarray,
    rect: QRectF,
    frac_a: float,
    frac_b: float,
    color: QColor,
    audio: np.ndarray | None = None,
    gain: float = 1.0,
) -> None:
    """Paint a min/max peak array across `rect` between two 0..1 positions."""
    if peaks is None or not len(peaks):
        return
    w = int(rect.width())
    if w <= 0:
        return
    mid = rect.center().y()
    amp = (rect.height() / 2 - 1) * gain

    total = len(peaks)
    b0, b1 = frac_a * total, frac_b * total
    per_px = (b1 - b0) / w

    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(color))

    if per_px >= 1.0 or audio is None:
        starts = (b0 + np.arange(w) * per_px).astype(np.int64)
        ends = np.maximum(starts + 1, (b0 + (np.arange(w) + 1) * per_px).astype(np.int64))
        np.clip(starts, 0, total - 1, out=starts)
        np.clip(ends, 1, total, out=ends)
        for x in range(w):
            seg = peaks[starts[x] : ends[x]]
            if not len(seg):
                continue
            lo = float(seg[:, 0].min())
            hi = float(seg[:, 1].max())
            y0 = mid - hi * amp
            y1 = mid - lo * amp
            p.drawRect(QRectF(rect.left() + x, y0, 1.0, max(1.0, y1 - y0)))
    else:
        # Zoomed in past bucket resolution — trace the real samples.
        n = len(audio)
        s0, s1 = frac_a * n, frac_b * n
        xs = np.linspace(s0, s1, w).astype(np.int64)
        np.clip(xs, 0, n - 1, out=xs)
        vals = audio[xs].mean(axis=1)
        pen = QPen(color)
        pen.setWidthF(1.2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        poly = QPolygonF(
            [QPointF(rect.left() + i, mid - float(v) * amp) for i, v in enumerate(vals)]
        )
        p.drawPolyline(poly)


def format_time(seconds: float, decimals: int = 3) -> str:
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}:{rest:0{3 + decimals}.{decimals}f}" if minutes else f"{rest:.{decimals}f}s"


class WaveformView(QWidget):
    """Zoomable waveform with slice markers and a freeform sample selection."""

    sliceSelected = Signal(int)  # index of the slice that was clicked
    markersChanged = Signal()
    markersAboutToChange = Signal()
    scrubbed = Signal(float)  # seconds
    selectionChanged = Signal(float, float)
    selectionFinished = Signal(float, float)
    viewChanged = Signal()
    playRequested = Signal()  # Enter / double-click on the ruler
    menuRequested = Signal(QPoint, float)  # global pos, time under the cursor

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.audio: np.ndarray | None = None
        self.peaks: np.ndarray | None = None
        self.duration = 0.0
        self.markers: list[float] = []
        self.selected = -1
        self.bpm: float | None = None
        self.clip_id: str | None = None
        # Optional category and true end per slice, filled in by an auto chop.
        # Without the ends a detected kick would select as "everything up to
        # the next kick" instead of as the kick.
        self.slice_kinds: dict[int, str] = {}
        self.slice_ends: dict[int, float] = {}
        # Spans rather than cut points — loops and drops, as (start, end, kind).
        self.regions: list[tuple[float, float, str]] = []

        self.view_a = 0.0  # visible window, fractions of the clip
        self.view_b = 1.0
        self.selection_start = 0.0
        self.selection_end = 0.0
        self.snap_mode = "zero crossing"
        self.amp_zoom = 1.0  # vertical zoom, ctrl+wheel
        self._hover_x: float | None = None
        self._drag_marker = -1
        self.cut_mode = False
        self._drag_selection: str | None = None
        self._panning: float | None = None
        self._scrubbing = False
        self._press_x = 0.0
        self._press_time = 0.0
        self._selection_origin = (0.0, 0.0)
        self._play_head: float | None = None

    # ── data ─────────────────────────────────────────────────
    def set_clip(self, audio, peaks, duration, markers, bpm=None, clip_id=None):
        self._trace_key = None
        self.audio = audio
        self.peaks = peaks
        self.duration = duration or 0.0
        self.markers = markers
        self.bpm = bpm
        self.clip_id = clip_id
        self.selected = -1
        self.slice_kinds = {}
        self.slice_ends = {}
        self.regions = []
        self.view_a, self.view_b = 0.0, 1.0
        self.selection_start = 0.0
        self.selection_end = self.duration
        self.selectionChanged.emit(self.selection_start, self.selection_end)
        self.viewChanged.emit()
        self.update()

    def set_playhead(self, seconds: float | None):
        if seconds == self._play_head:
            return
        self._play_head = seconds
        self.update()

    # ── geometry ─────────────────────────────────────────────
    def _span(self) -> float:
        return max(1e-9, self.view_b - self.view_a)

    def wave_rect(self) -> QRectF:
        """The area the waveform itself occupies, below the ruler."""
        return QRectF(0, RULER_H, self.width(), max(1.0, self.height() - RULER_H))

    def x_to_time(self, x: float) -> float:
        return (self.view_a + (x / max(1, self.width())) * self._span()) * self.duration

    def time_to_x(self, t: float) -> float:
        if self.duration <= 0:
            return 0.0
        return ((t / self.duration) - self.view_a) / self._span() * self.width()

    def selection(self) -> tuple[float, float]:
        return self.selection_start, self.selection_end

    def set_selection(
        self,
        start: float,
        end: float,
        *,
        emit: bool = True,
        ensure_visible: bool = False,
        snap: bool = False,
    ) -> None:
        """Set the playable range, clamped to the loaded clip."""
        if self.duration <= 0:
            self.selection_start = self.selection_end = 0.0
            return
        start = min(max(0.0, float(start)), self.duration)
        end = min(max(0.0, float(end)), self.duration)
        if snap:
            start, end = self._snap_time(start), self._snap_time(end)
        if end < start:
            start, end = end, start
        minimum = min(0.001, self.duration)
        if end - start < minimum:
            end = min(self.duration, start + minimum)
            start = max(0.0, end - minimum)
        self.selection_start = round(start, 6)
        self.selection_end = round(end, 6)
        if ensure_visible:
            self.zoom_to_selection(padding=0.12)
        if emit:
            self.selectionChanged.emit(self.selection_start, self.selection_end)
        self.update()

    def _snap_time(self, seconds: float) -> float:
        """Snap a trim point to a click-free crossing or a musical grid."""
        seconds = min(max(0.0, seconds), self.duration)
        if seconds <= 0.0 or seconds >= self.duration:
            return seconds
        if self.snap_mode == "off" or self.duration <= 0:
            return seconds
        if self.snap_mode.endswith("grid") and self.bpm:
            beat = 60.0 / self.bpm
            divisions = {"1/16 grid": 4, "1/8 grid": 2, "beat grid": 1}
            step = beat / divisions[self.snap_mode]
            return min(self.duration, max(0.0, round(seconds / step) * step))
        if self.snap_mode != "zero crossing" or self.audio is None or not len(self.audio):
            return seconds

        rate = len(self.audio) / self.duration
        centre = int(round(seconds * rate))
        radius = max(1, int(rate * 0.012))
        lo, hi = max(0, centre - radius), min(len(self.audio) - 1, centre + radius)
        if hi <= lo:
            return seconds
        segment = np.asarray(self.audio[lo : hi + 1])
        channels = segment.reshape(len(segment), -1)

        # A mono sum is unsafe here: [+1, -1] looks exactly like silence even
        # though cutting either channel at that frame produces a full-scale
        # edge.  Minimise the worst channel instead, which finds the quietest
        # joint cut point without letting one channel mask another.
        joint_level = np.max(np.abs(channels), axis=1).astype(np.float64, copy=False)
        joint_level = np.nan_to_num(joint_level, nan=np.inf, posinf=np.inf, neginf=np.inf)
        samples = np.arange(lo, hi + 1, dtype=np.int64)
        distance = np.abs(samples - centre)

        # Twelve milliseconds is already the hard locality limit.  This small
        # cost also prevents an almost indistinguishable amplitude improvement
        # at the edge of that window from making a trim handle feel jumpy.
        score = joint_level + 0.05 * (distance / max(1, radius))
        nearest = int(samples[np.argmin(score)])
        return nearest / rate

    def fit(self) -> None:
        self.view_a, self.view_b = 0.0, 1.0
        self.viewChanged.emit()
        self.update()

    def zoom_by(self, factor: float, focus: float | None = None) -> None:
        """Zoom about a point given as a 0..1 position in the file."""
        if self.duration <= 0:
            return
        span = self._span()
        centre = focus if focus is not None else self.view_a + span / 2
        new_span = min(1.0, max(0.0002, span * factor))
        a = min(max(0.0, centre - (centre - self.view_a) * (new_span / span)), 1.0 - new_span)
        self.view_a, self.view_b = a, a + new_span
        self.viewChanged.emit()
        self.update()

    def pan_by(self, fraction: float) -> None:
        span = self._span()
        a = min(max(0.0, self.view_a + span * fraction), 1.0 - span)
        self.view_a, self.view_b = a, a + span
        self.viewChanged.emit()
        self.update()

    def centre_on(self, seconds: float) -> None:
        if self.duration <= 0:
            return
        span = self._span()
        a = min(max(0.0, seconds / self.duration - span / 2), 1.0 - span)
        self.view_a, self.view_b = a, a + span
        self.viewChanged.emit()
        self.update()

    def zoom_to_selection(self, padding: float = 0.08) -> None:
        if self.duration <= 0 or self.selection_end <= self.selection_start:
            return
        a = self.selection_start / self.duration
        b = self.selection_end / self.duration
        pad = max(0.0001, (b - a) * max(0.0, padding))
        a, b = max(0.0, a - pad), min(1.0, b + pad)
        if b - a < 0.0002:
            centre = (a + b) / 2
            a = max(0.0, centre - 0.0001)
            b = min(1.0, a + 0.0002)
            a = max(0.0, b - 0.0002)
        self.view_a, self.view_b = a, b
        self.viewChanged.emit()
        self.update()

    def slice_bounds(self, index: int) -> tuple[float, float]:
        if not self.markers or index < 0:
            return 0.0, self.duration
        index = min(index, len(self.markers) - 1)
        s = self.markers[index]
        nxt = self.markers[index + 1] if index + 1 < len(self.markers) else self.duration
        detected = self.slice_ends.get(index)
        e = min(detected, nxt) if detected else nxt
        return s, max(e, min(s + 0.001, self.duration))

    def slice_at(self, t: float) -> int:
        if not self.markers or t < self.markers[0]:
            return -1
        i = 0
        while i + 1 < len(self.markers) and self.markers[i + 1] <= t:
            i += 1
        return i

    def all_slices(self) -> list[tuple[float, float]]:
        if not self.markers:
            return [(0.0, self.duration)]
        return [self.slice_bounds(i) for i in range(len(self.markers))]

    def select_slice(self, index: int, *, audition: bool = True) -> None:
        """Select a slice and make it the pad range."""
        if not self.markers:
            return
        index = max(0, min(index, len(self.markers) - 1))
        self.selected = index
        start, end = self.slice_bounds(index)
        self.set_selection(start, end)
        if audition:
            self.sliceSelected.emit(index)
        self.update()

    def _marker_near(self, x: float) -> int:
        tol = 6
        best, best_d = -1, 1e9
        for i, m in enumerate(self.markers):
            d = abs(self.time_to_x(m) - x)
            if d < tol and d < best_d:
                best, best_d = i, d
        return best

    def _selection_handle_near(self, x: float) -> str | None:
        if abs(self.time_to_x(self.selection_start) - x) <= 9:
            return "start"
        if abs(self.time_to_x(self.selection_end) - x) <= 9:
            return "end"
        return None

    def _in_band(self, y: float) -> bool:
        return y >= self.height() - BAND_H

    def _in_ruler(self, y: float) -> bool:
        return y < RULER_H

    def _set_drag_selection(self, mode: str, t: float) -> None:
        minimum = min(0.001, self.duration)
        if mode == "new":
            self.set_selection(self._press_time, t, snap=True)
        elif mode == "start":
            self.set_selection(min(t, self.selection_end - minimum), self.selection_end, snap=True)
        elif mode == "end":
            self.set_selection(
                self.selection_start, max(t, self.selection_start + minimum), snap=True
            )
        elif mode == "move":
            old_start, old_end = self._selection_origin
            length = old_end - old_start
            start = old_start + t - self._press_time
            start = min(max(0.0, start), max(0.0, self.duration - length))
            self.set_selection(start, start + length)

    # ── interaction ──────────────────────────────────────────
    def wheelEvent(self, ev):
        if self.duration <= 0:
            return
        delta = ev.angleDelta().y()
        mods = ev.modifiers()
        if mods & Qt.ControlModifier:
            # Vertical zoom, as in every audio editor: makes a quiet passage
            # readable without touching the time axis.
            self.amp_zoom = float(np.clip(self.amp_zoom * (1.25 if delta > 0 else 0.8), 0.25, 24.0))
            self.update()
            return
        if mods & Qt.ShiftModifier:
            self.pan_by(0.15 * (-1 if delta > 0 else 1))
            return
        focus = self.view_a + (ev.position().x() / max(1, self.width())) * self._span()
        self.zoom_by(0.8 if delta > 0 else 1.25, focus)

    def mousePressEvent(self, ev):
        if self.duration <= 0:
            return
        x = ev.position().x()
        y = ev.position().y()
        t = self.x_to_time(x)
        mods = ev.modifiers()
        self.setFocus(Qt.MouseFocusReason)

        if ev.button() == Qt.MiddleButton:
            self._panning = x
            self.setCursor(Qt.ClosedHandCursor)
            return

        if ev.button() == Qt.RightButton:
            near = self._marker_near(x)
            if near >= 0 and not self._in_band(y):
                self.markersAboutToChange.emit()
                self.markers.pop(near)
                self.slice_kinds.pop(near, None)
                self.selected = min(self.selected, len(self.markers) - 1)
                self.markersChanged.emit()
                self.update()
            else:
                self.menuRequested.emit(
                    ev.globalPosition().toPoint(), min(max(0.0, t), self.duration)
                )
            return

        if self._in_ruler(y):
            self._scrubbing = True
            self.scrubbed.emit(min(max(0.0, t), self.duration))
            self.set_playhead(min(max(0.0, t), self.duration))
            return

        if mods & Qt.ShiftModifier or self.cut_mode:
            self.add_marker(t)
            return

        near = self._marker_near(x)
        if near >= 0 and not self._in_band(y):
            self.markersAboutToChange.emit()
            self._drag_marker = near
            return

        self._press_x = x
        self._press_time = min(max(0.0, t), self.duration)
        self._selection_origin = self.selection()
        in_band = self._in_band(y)
        handle = self._selection_handle_near(x) if in_band else None
        inside = self.time_to_x(self.selection_start) < x < self.time_to_x(self.selection_end)
        if handle:
            self._drag_selection = handle
        elif in_band and inside:
            if mods & Qt.ControlModifier:
                self._start_range_drag()
                return
            self._drag_selection = "move"
        else:
            self._drag_selection = "new"
            self.set_selection(self._press_time, self._press_time + min(0.001, self.duration))

    def _start_range_drag(self) -> None:
        """Carry the chosen range onto a pad or into the arrangement."""
        if not self.clip_id:
            return
        start, end = self.selection()
        mime = QMimeData()
        mime.setData(RANGE_MIME, f"{self.clip_id}|{start:.6f}|{end:.6f}".encode())
        mime.setText(f"{start:.3f}s → {end:.3f}s")

        pix = QPixmap(190, 22)
        pix.fill(q("bg3"))
        p = QPainter(pix)
        p.setPen(q("accent"))
        p.drawRect(0, 0, 189, 21)
        p.drawText(
            pix.rect().adjusted(6, 0, -4, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            f"range  {end - start:.3f}s",
        )
        p.end()

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(pix)
        drag.exec(Qt.CopyAction)

    def mouseMoveEvent(self, ev):
        self._hover_x = ev.position().x()
        y = ev.position().y()
        if self._panning is not None:
            delta = (self._panning - self._hover_x) / max(1, self.width())
            self.pan_by(delta)
            self._panning = self._hover_x
            return
        if self._scrubbing and self.duration > 0:
            t = min(max(0.0, self.x_to_time(self._hover_x)), self.duration)
            self.scrubbed.emit(t)
            self.set_playhead(t)
            return
        if self._drag_marker >= 0 and self.duration > 0:
            t = min(max(0.0, self.x_to_time(self._hover_x)), self.duration)
            if not (ev.modifiers() & Qt.AltModifier):
                t = self._snap_time(t)
            self.markers[self._drag_marker] = round(t, 5)
            self.update()
            return
        if self._drag_selection and self.duration > 0:
            t = min(max(0.0, self.x_to_time(self._hover_x)), self.duration)
            self._set_drag_selection(self._drag_selection, t)
            if self._drag_selection in ("start", "end"):
                self.setCursor(Qt.SplitHCursor)
            elif self._drag_selection == "move":
                self.setCursor(Qt.ClosedHandCursor)
            else:
                self.setCursor(Qt.CrossCursor)
            return

        near = self._marker_near(self._hover_x)
        in_band = self._in_band(y)
        handle = self._selection_handle_near(self._hover_x) if in_band else None
        inside = (
            self.time_to_x(self.selection_start)
            < self._hover_x
            < self.time_to_x(self.selection_end)
        )
        if self._in_ruler(y):
            cursor = Qt.PointingHandCursor
        elif handle or (near >= 0 and not in_band):
            cursor = Qt.SplitHCursor
        elif in_band and inside:
            cursor = Qt.OpenHandCursor
        else:
            cursor = Qt.CrossCursor
        self.setCursor(cursor)
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._panning is not None:
            self._panning = None
            self.unsetCursor()
            return
        if self._scrubbing:
            self._scrubbing = False
            return
        if self._drag_marker >= 0:
            self._drag_marker = -1
            self.markers.sort()
            self.markersChanged.emit()
            self.update()
            return
        if self._drag_selection:
            moved = abs(ev.position().x() - self._press_x) > 3
            mode = self._drag_selection
            self._drag_selection = None
            self.unsetCursor()
            if mode == "new" and not moved:
                t = min(max(0.0, self.x_to_time(ev.position().x())), self.duration)
                idx = self.slice_at(t)
                self.selected = idx
                start, end = self.slice_bounds(idx)
                self.set_selection(start, end)
                self.sliceSelected.emit(idx)
            else:
                self.selectionFinished.emit(*self.selection())
            self.update()

    def mouseDoubleClickEvent(self, ev):
        if self.duration <= 0:
            return
        if self._in_ruler(ev.position().y()):
            self.playRequested.emit()
            return
        self.add_marker(self.x_to_time(ev.position().x()))

    def add_marker(self, t: float) -> None:
        t = min(max(0.0, self._snap_time(t)), self.duration)
        t = round(t, 5)
        if self.duration <= 0 or t >= self.duration or t in self.markers:
            return
        self.markersAboutToChange.emit()
        if not self.markers and t > 0:
            self.markers.append(0.0)
        self.markers.append(t)
        self.markers.sort()
        self.markersChanged.emit()
        self.update()

    def leaveEvent(self, ev):
        self._hover_x = None
        self.update()

    # ── keyboard ─────────────────────────────────────────────
    def keyPressEvent(self, ev):
        """Editor keys. Everything global (space, pads) is left alone."""
        if self.duration <= 0:
            super().keyPressEvent(ev)
            return
        key = ev.key()
        mods = ev.modifiers()
        # Fine with no modifier, coarse with shift, on the grid with ctrl.
        step = 0.001 if not mods & Qt.ShiftModifier else 0.02
        if mods & Qt.ControlModifier and self.bpm:
            step = (60.0 / self.bpm) / 4
        start, end = self.selection()

        if key in (Qt.Key_Left, Qt.Key_Right):
            sign = -1.0 if key == Qt.Key_Left else 1.0
            if mods & Qt.AltModifier:  # move the whole range
                length = end - start
                new_start = min(max(0.0, start + sign * step), self.duration - length)
                self.set_selection(new_start, new_start + length)
            else:  # nudge the end point
                self.set_selection(start, end + sign * step)
            self.selectionFinished.emit(*self.selection())
            return
        if key in (Qt.Key_Up, Qt.Key_Down):  # previous / next slice
            if self.markers:
                self.select_slice(self.selected + (1 if key == Qt.Key_Up else -1))
            return
        if key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(0.7, (start + end) / 2 / max(1e-9, self.duration))
            return
        if key == Qt.Key_Minus:
            self.zoom_by(1.4, (start + end) / 2 / max(1e-9, self.duration))
            return
        if key in (Qt.Key_0, Qt.Key_F):
            self.fit()
            return
        if key == Qt.Key_Z:
            self.zoom_to_selection()
            return
        if key == Qt.Key_Home:
            self.set_selection(0.0, end - start)
            self.centre_on(0.0)
            return
        if key == Qt.Key_End:
            self.set_selection(self.duration - (end - start), self.duration)
            self.centre_on(self.duration)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.playRequested.emit()
            return
        if key == Qt.Key_M and self._hover_x is not None:
            self.add_marker(self.x_to_time(self._hover_x))
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace) and self.markers:
            index = self.selected if self.selected >= 0 else -1
            if 0 <= index < len(self.markers):
                self.markersAboutToChange.emit()
                self.markers.pop(index)
                self.slice_kinds.pop(index, None)
                self.selected = min(index, len(self.markers) - 1)
                self.markersChanged.emit()
                self.update()
            return
        super().keyPressEvent(ev)

    # ── painting ─────────────────────────────────────────────
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        full = QRectF(self.rect())
        p.fillRect(full, q("canvas"))

        if self.audio is None or self.duration <= 0:
            p.setPen(q("dim2"))
            p.drawText(full, Qt.AlignCenter, "select a sample in the browser")
            return

        r = self.wave_rect()
        sx0 = self.time_to_x(self.selection_start)
        sx1 = self.time_to_x(self.selection_end)

        # Quiet amplitude guides give the editor the depth of a scope without
        # fighting the musical beat grid drawn later.
        p.setPen(QPen(q("fg", 12)))
        for fraction in (0.25, 0.5, 0.75):
            y = r.top() + r.height() * fraction
            p.drawLine(int(r.left()), int(y), int(r.right()), int(y))

        self._prepare_annotations(r)
        p.drawPixmap(0, 0, self._slice_layer)
        self._paint_slices(p, r)
        chosen = QRectF(sx0, r.top(), sx1 - sx0, r.height())
        wash = QLinearGradient(chosen.topLeft(), chosen.bottomLeft())
        wash.setColorAt(0.0, q("accent", 18))
        wash.setColorAt(0.5, q("accent", 48))
        wash.setColorAt(1.0, q("accent", 20))
        p.fillRect(chosen, QBrush(wash))

        # Cache only the waveform traces. Selection, markers and the playhead
        # remain live overlays; moving them never rescans the record's peaks.
        self._prepare_traces(r)
        p.drawPixmap(0, 0, self._dim_trace)
        p.save()
        p.setClipRect(QRectF(sx0, r.top(), max(1.0, sx1 - sx0), r.height()))
        p.drawPixmap(0, 0, self._selected_trace)
        p.restore()

        p.setPen(QPen(q("fg", 30)))
        p.drawLine(0, int(r.center().y()), int(r.width()), int(r.center().y()))

        self._paint_grid(p, r)
        self._paint_regions(p, r)
        self._paint_ruler(p)
        p.drawPixmap(0, 0, self._marker_layer)
        self._paint_markers(p, r)
        self._paint_band(p, r, sx0, sx1)

        if self._play_head is not None:
            x = self.time_to_x(self._play_head)
            p.setPen(QPen(q("ok"), 1.5))
            p.drawLine(int(x), int(RULER_H), int(x), int(full.height()))

        if self._hover_x is not None:
            p.setPen(QPen(q("fg", 70)))
            p.drawLine(int(self._hover_x), int(RULER_H), int(self._hover_x), int(full.height()))
            p.setPen(q("dim"))
            p.drawText(
                QPointF(min(self._hover_x + 5, self.width() - 62), self.height() - BAND_H - 4),
                format_time(self.x_to_time(self._hover_x)),
            )

    def _prepare_traces(self, rect: QRectF) -> None:
        ratio = self.devicePixelRatioF()
        key = (
            id(self.audio),
            id(self.peaks),
            self.width(),
            self.height(),
            ratio,
            self.view_a,
            self.view_b,
            self.amp_zoom,
            q("wave").rgba(),
            q("wavedim").rgba(),
        )
        if key == getattr(self, "_trace_key", None):
            return
        for attribute, passes in (
            ("_dim_trace", ((q("wavedim"), self.amp_zoom),)),
            (
                "_selected_trace",
                ((q("wave", 52), self.amp_zoom * 1.035), (q("wave"), self.amp_zoom)),
            ),
        ):
            image = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            image.setDevicePixelRatio(ratio)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            for colour, gain in passes:
                draw_peaks(
                    painter, self.peaks, rect, self.view_a, self.view_b, colour, self.audio, gain
                )
            painter.end()
            setattr(self, attribute, image)
        self._trace_key = key

    def _prepare_annotations(self, rect: QRectF) -> None:
        ratio = self.devicePixelRatioF()
        key = (
            self.width(),
            self.height(),
            ratio,
            self.duration,
            self.view_a,
            self.view_b,
            tuple(self.markers),
            tuple(self.slice_ends.items()),
            tuple(self.slice_kinds.items()),
            q("fg").rgba(),
            q("accent").rgba(),
            q("on_accent").rgba(),
            self.font().toString(),
        )
        if key == getattr(self, "_annotation_key", None):
            return
        for attribute, paint in (
            ("_slice_layer", self._paint_slices),
            ("_marker_layer", self._paint_markers),
        ):
            layer = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            layer.setDevicePixelRatio(ratio)
            layer.fill(Qt.transparent)
            painter = QPainter(layer)
            paint(painter, rect, base_only=True)
            painter.end()
            setattr(self, attribute, layer)
        self._annotation_key = key

    def _paint_slices(self, p: QPainter, r: QRectF, *, base_only=False) -> None:
        """Alternating region tints so slice boundaries read at a glance."""
        if not self.markers:
            return
        hover_index = (
            self.slice_at(self.x_to_time(self._hover_x)) if self._hover_x is not None else -2
        )
        for i, (s, e) in enumerate(self.all_slices()):
            if not base_only and i not in (hover_index, self.selected):
                continue
            x0, x1 = self.time_to_x(s), self.time_to_x(e)
            if x1 < -20 or x0 > self.width() + 20:
                continue
            kind = self.slice_kinds.get(i)
            base = QColor(hit_color(kind)) if kind else q("fg")
            alpha = (26 if i % 2 else 14) if base_only else 0
            if not base_only and i == hover_index:
                alpha += 16
            if not base_only and i == self.selected:
                alpha += 22
            base.setAlpha(alpha)
            p.fillRect(QRectF(x0, r.top(), max(1.0, x1 - x0), r.height()), base)

    def _paint_regions(self, p: QPainter, r: QRectF) -> None:
        """Loops and drops: bracketed spans along the top of the wave area."""
        if not self.regions:
            return
        font = QFont(self.font())
        font.setPointSizeF(7.0)
        p.setFont(font)
        for i, (s, e, kind) in enumerate(self.regions):
            x0, x1 = self.time_to_x(s), self.time_to_x(e)
            if x1 < -40 or x0 > self.width() + 40:
                continue
            colour = QColor(hit_color(kind))
            y = r.top() + 13 + (i % 3) * 13  # stagger so overlaps stay readable
            wash = QColor(colour)
            wash.setAlpha(22)
            p.fillRect(QRectF(x0, r.top(), max(1.0, x1 - x0), r.height()), wash)
            p.setPen(QPen(colour, 1))
            p.drawLine(int(x0), int(y), int(x1), int(y))
            p.drawLine(int(x0), int(y - 3), int(x0), int(y + 3))
            p.drawLine(int(x1), int(y - 3), int(x1), int(y + 3))
            if x1 - x0 > 46:
                label = f"{kind} {e - s:.2f}s"
                box = QRectF(x0 + 3, y - 11, min(x1 - x0 - 4, 90), 11)
                p.fillRect(box, q("canvas"))
                p.setPen(colour)
                p.drawText(box, Qt.AlignVCenter | Qt.AlignLeft, label)

    def _paint_grid(self, p: QPainter, r: QRectF) -> None:
        if not self.bpm:
            return
        beat = 60.0 / self.bpm
        t0, t1 = self.x_to_time(0), self.x_to_time(self.width())
        if (t1 - t0) / beat > 400:
            return
        first = int(t0 / beat)
        for b in range(first, int(t1 / beat) + 2):
            x = self.time_to_x(b * beat)
            downbeat = b % 4 == 0
            p.setPen(QPen(q("fg", 40 if downbeat else 20)))
            top = r.top() if downbeat else r.bottom() - 14
            p.drawLine(int(x), int(top), int(x), int(r.bottom()))

    def _paint_ruler(self, p: QPainter) -> None:
        """Time along the top, in bars when a tempo is known and seconds when not."""
        rect = QRectF(0, 0, self.width(), RULER_H)
        p.fillRect(rect, q("bg3"))
        p.setPen(QPen(q("line")))
        p.drawLine(0, int(RULER_H), self.width(), int(RULER_H))

        font = QFont(self.font())
        font.setPointSizeF(7.0)
        p.setFont(font)
        p.setPen(q("dim2"))

        t0, t1 = self.x_to_time(0), self.x_to_time(self.width())
        visible = max(1e-6, t1 - t0)
        if self.bpm:
            bar = (60.0 / self.bpm) * 4
            step_bars = 1
            while visible / (bar * step_bars) > 24:
                step_bars *= 2
            step = bar * step_bars
            first = int(t0 / step)
            for i in range(first, int(t1 / step) + 2):
                t = i * step
                x = self.time_to_x(t)
                p.drawLine(int(x), int(RULER_H - 5), int(x), int(RULER_H))
                p.drawText(QPointF(x + 3, RULER_H - 6), f"{i * step_bars + 1}")
        else:
            step = 10.0 ** np.floor(np.log10(max(visible / 8, 1e-4)))
            while visible / step > 20:
                step *= 2
            first = int(t0 / step)
            for i in range(first, int(t1 / step) + 2):
                t = i * step
                x = self.time_to_x(t)
                p.drawLine(int(x), int(RULER_H - 5), int(x), int(RULER_H))
                p.drawText(QPointF(x + 3, RULER_H - 6), format_time(t, 1))

        if self._play_head is not None:
            x = self.time_to_x(self._play_head)
            head = QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 8)])
            p.setPen(Qt.NoPen)
            p.setBrush(q("ok"))
            p.drawPolygon(head)

    def _paint_markers(self, p: QPainter, r: QRectF, *, base_only=False) -> None:
        font = QFont(self.font())
        font.setPointSizeF(7.5)
        p.setFont(font)
        count = len(self.markers)
        for i, m in enumerate(self.markers):
            if not base_only and i != self.selected:
                continue
            x = self.time_to_x(m)
            if x < -40 or x > self.width() + 40:
                continue
            active = not base_only and i == self.selected
            kind = self.slice_kinds.get(i)
            colour = QColor(hit_color(kind)) if kind else q("accent")
            line = QColor(colour)
            if not active:
                line.setAlpha(150)
            p.setPen(QPen(line, 2 if active else 1))
            p.drawLine(int(x), int(r.top()), int(x), int(r.bottom()))

            label = f"{i + 1}" if not kind else f"{i + 1} {kind}"
            width = 16 if not kind else 12 + 5.4 * len(label)
            # Only tag a slice when the next one is far enough away to leave
            # room. Zoomed out over a whole song the labels would otherwise
            # overprint each other into an unreadable smear.
            room = self.time_to_x(self.markers[i + 1]) - x if i + 1 < count else self.width() - x
            if not active and room < width + 3:
                continue
            tag = QRectF(x, r.top(), width, 11)
            p.fillRect(
                tag, colour if active else QColor(colour.red(), colour.green(), colour.blue(), 190)
            )
            p.setPen(q("on_accent"))
            p.drawText(tag, Qt.AlignCenter, label)

    def _paint_band(self, p: QPainter, r: QRectF, sx0: float, sx1: float) -> None:
        band_y = self.height() - BAND_H
        p.fillRect(QRectF(sx0, band_y, max(2.0, sx1 - sx0), BAND_H), q("accent", 78))
        p.setPen(QPen(q("accent_hi"), 2))
        p.drawLine(int(sx0), int(r.top()), int(sx0), int(self.height()))
        p.drawLine(int(sx1), int(r.top()), int(sx1), int(self.height()))
        for x, label in ((sx0, "S"), (sx1, "E")):
            box_x = x if label == "S" else x - 18
            p.fillRect(QRectF(box_x, band_y, 18, BAND_H), q("accent"))
            p.setPen(q("on_accent"))
            p.drawText(QRectF(box_x, band_y, 18, BAND_H), Qt.AlignCenter, label)

        length = self.selection_end - self.selection_start
        if sx1 - sx0 > 90:
            p.setPen(q("on_accent"))
            p.drawText(
                QRectF(sx0 + 20, band_y, sx1 - sx0 - 40, BAND_H), Qt.AlignCenter, f"{length:.3f}s"
            )


class NavStrip(QWidget):
    """Whole-file overview with a draggable view window."""

    viewMoved = Signal(float)  # new centre, 0..1

    def __init__(self, view: WaveformView, parent=None):
        super().__init__(parent)
        self.view = view
        self.overview: np.ndarray | None = None  # RMS contour of the whole clip
        self.setFixedHeight(46)
        self.setCursor(Qt.PointingHandCursor)
        self.view.viewChanged.connect(self.update)
        self.view.selectionChanged.connect(lambda _a, _b: self.update())

    def set_overview(self, contour) -> None:
        self.overview = contour
        self.update()

    def _draw_contour(self, p: QPainter, r: QRectF) -> None:
        contour = self.overview
        if contour is None or not len(contour):
            draw_peaks(p, self.view.peaks, r, 0.0, 1.0, q("wavedim"))
            return
        w = max(1, int(r.width()))
        idx = np.linspace(0, len(contour) - 1, w).astype(np.int64)
        vals = contour[idx]
        mid = r.center().y()
        amp = r.height() / 2 - 1
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(q("wavedim")))
        for x, v in enumerate(vals):
            h = float(v) * amp
            p.drawRect(QRectF(r.left() + x, mid - h, 1.0, max(1.0, h * 2)))

    def _move_to(self, x: float):
        span = self.view.view_b - self.view.view_a
        centre = min(max(0.0, x / max(1, self.width())), 1.0)
        a = min(max(0.0, centre - span / 2), 1.0 - span)
        self.view.view_a, self.view.view_b = a, a + span
        self.view.viewChanged.emit()
        self.view.update()
        self.update()

    def mousePressEvent(self, ev):
        self._move_to(ev.position().x())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton:
            self._move_to(ev.position().x())

    def paintEvent(self, ev):
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, q("canvas"))
        if self.view.audio is None:
            return
        self._draw_contour(p, r)
        duration = self.view.duration
        if duration > 0:
            # Detected slices show here too, so the whole song reads at a glance.
            for i, (s, _e) in enumerate(self.view.all_slices()):
                kind = self.view.slice_kinds.get(i)
                if not kind:
                    continue
                colour = QColor(hit_color(kind))
                colour.setAlpha(150)
                x0 = s / duration * self.width()
                p.fillRect(QRectF(x0, 6.0, 1.5, r.height() - 6.0), colour)
            # Regions ride in a thin lane along the top so a dozen overlapping
            # loops cannot wash the whole overview blue.
            for s, e, kind in self.view.regions:
                colour = QColor(hit_color(kind))
                x0 = s / duration * self.width()
                p.fillRect(QRectF(x0, 0, max(2.0, (e - s) / duration * self.width()), 5.0), colour)
            s0 = self.view.selection_start / duration * self.width()
            s1 = self.view.selection_end / duration * self.width()
            p.fillRect(QRectF(s0, 0, max(1.0, s1 - s0), r.height()), q("accent", 34))
            if self.view._play_head is not None:
                x = self.view._play_head / duration * self.width()
                p.setPen(QPen(q("ok"), 1))
                p.drawLine(int(x), 0, int(x), int(r.height()))
        x0 = self.view.view_a * self.width()
        x1 = self.view.view_b * self.width()
        width = max(2.0, x1 - x0)
        # Shade what is *not* on screen instead of tinting what is: covering the
        # visible span in blue is exactly the case where the picture matters.
        p.fillRect(QRectF(0, 0, x0, r.height()), q("bg", 150))
        p.fillRect(QRectF(x1, 0, self.width() - x1, r.height()), q("bg", 150))
        # drawRect fills with the current brush, and the waveform pass left one
        # set — without this the outline floods the whole strip.
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(q("accent"), 1))
        p.drawRect(QRectF(x0, 0.5, width, r.height() - 1))
