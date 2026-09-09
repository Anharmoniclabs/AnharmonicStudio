"""Waveform and pitch guide for offline vocal tuning; no input device access."""

from dataclasses import replace
import math
import threading

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDial, QScrollBar, QWidget

from ..model import VocalSettings
from ..vocal import allowed_notes, analyze_pitch, note_name
from . import theme
from .window_client import emit_if_alive


class TuningDial(QDial):
    """Native dial interaction with a high-contrast, theme-aware indicator."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ring = QRectF(8, 8, self.width() - 16, self.height() - 16)
        fraction = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        p.setPen(QPen(theme.q("line"), 4, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 225 * 16, -270 * 16)
        p.setPen(QPen(theme.q("accent"), 4, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 225 * 16, round(-270 * fraction * 16))
        p.setPen(QPen(theme.q("line"), 1))
        p.setBrush(theme.q("bg2"))
        p.drawEllipse(ring.adjusted(7, 7, -7, -7))
        angle = math.radians(225 - fraction * 270)
        center = ring.center()
        tip = QPointF(center.x() + 15 * math.cos(angle), center.y() - 15 * math.sin(angle))
        p.setPen(QPen(theme.q("fg"), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(center, tip)
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(theme.q("accent"), 1, Qt.DotLine))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        p.end()


def correction_guide(analysis, settings):
    """Approximate the tuning intention, not a measurement of rendered audio."""
    detected = analysis.detected_midi
    targets = detected.copy()
    voiced = np.isfinite(detected) & (analysis.confidence >= 0.18)
    choices = allowed_notes(settings)
    for index in np.flatnonzero(voiced):
        targets[index] = choices[np.argmin(np.abs(choices - detected[index]))] + settings.transpose
    if not settings.enabled:
        return detected.copy()
    result = detected.copy()
    state = 0.0
    for index in range(len(detected)):
        if not voiced[index]:
            state = 0.0
            continue
        dt = float(analysis.times[index] - analysis.times[index - 1]) if index else 0.01
        smooth = 1.0 if settings.retune_ms <= 0 else 1.0 - np.exp(-dt * 1000 / settings.retune_ms)
        depth = settings.strength * (1.0 - settings.humanize * analysis.confidence[index])
        state += ((targets[index] - detected[index]) * depth - state) * smooth
        result[index] += state * settings.mix
    return result


class VocalPitchView(QWidget):
    """Real source waveform and detected pitch with an interactive time selection."""

    finished = Signal(int, object, str)
    selectionChanged = Signal(float, float)
    MIN_NOTE_HEIGHT = 18.0
    PITCH_TOP = 128.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 380)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Vocal waveform and pitch editor")
        self.setToolTip(
            "Drag over the waveform to select a listening range. Wheel over the waveform "
            "to zoom time; wheel over the notes to scroll pitch. Double-click to fit the take."
        )
        self.clip_id = None
        self.audio = None
        self.sr = 48000
        self.duration = 0.0
        self.peaks = np.empty(0)
        self.analysis = None
        self.guide = np.empty(0)
        self.settings = VocalSettings()
        self.selection = (0.0, 0.0)
        self.view_start = 0.0
        self.view_end = 1.0
        self.cursor = 0.0
        self._anchor = None
        self._generation = 0
        self._cancel = None
        self.message = "Choose a recorded take to see its waveform and pitch."
        self.note_height = 24.0
        self.pitch_center = 60.0
        self._pitch_auto_fit = True
        self.pitch_scroll = QScrollBar(Qt.Vertical, self)
        self.pitch_scroll.setAccessibleName("Visible pitch range")
        self.pitch_scroll.setToolTip("Scroll to higher or lower notes")
        self.pitch_scroll.valueChanged.connect(self._scroll_pitch)
        self._sync_pitch_scroll()
        self.finished.connect(self._finished)

    def set_source(self, clip_id, audio, sr):
        if clip_id == self.clip_id and audio is self.audio:
            return
        self.shutdown()
        self.clip_id, self.audio, self.sr = clip_id, audio, sr
        self.analysis = None
        self.guide = np.empty(0)
        self.selection = (0.0, 0.0)
        self.selectionChanged.emit(0.0, 0.0)
        self.duration = len(audio) / sr if audio is not None else 0.0
        # At most 2,000 displayed peak bins; no full-length copy on the GUI thread.
        if audio is not None and len(audio):
            stride = max(1, int(np.ceil(len(audio) / 2000)))
            self.peaks = np.array(
                [
                    np.max(np.abs(audio[start : start + stride]))
                    for start in range(0, len(audio), stride)
                ]
            )
        else:
            self.peaks = np.empty(0)
        self.message = (
            "Analyzing recorded pitch…"
            if self.duration
            else "Choose a recorded take to see its waveform and pitch."
        )
        self.fit()
        if self.isVisible():
            self.analyze()

    def set_settings(self, settings):
        range_changed = (settings.low_note, settings.high_note) != (
            self.settings.low_note,
            self.settings.high_note,
        )
        self.settings = replace(settings)
        if range_changed:
            self.shutdown()
            self.analysis = None
            if self.isVisible():
                self.analyze()
        if self.analysis is not None:
            self.guide = correction_guide(self.analysis, self.settings)
        if self._pitch_auto_fit:
            self.fit_pitch()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self.analysis is None:
            self.analyze()

    def analyze(self):
        if self.audio is None or not len(self.audio) or self._cancel is not None:
            return
        self._generation += 1
        generation = self._generation
        cancel = self._cancel = threading.Event()
        audio, settings, sr = self.audio, replace(self.settings), self.sr
        self.message = "Analyzing recorded pitch…"
        self.update()

        def worker():
            try:
                result = analyze_pitch(audio, settings, sr, cancelled=cancel.is_set)
                emit_if_alive(self, "finished", generation, result, "")
            except Exception as exc:
                emit_if_alive(self, "finished", generation, None, str(exc))

        threading.Thread(target=worker, daemon=True, name="vocal-pitch-view").start()

    def _finished(self, generation, analysis, error):
        if generation != self._generation:
            return
        self._cancel = None
        self.analysis = analysis
        self.guide = (
            correction_guide(analysis, self.settings) if analysis is not None else np.empty(0)
        )
        self.message = (
            f"Could not analyze pitch: {error}"
            if error
            else "No clear vocal pitch detected. Try a dry solo vocal or a wider vocal range."
            if analysis.voiced_fraction < 0.01
            else ""
        )
        self.fit_pitch()
        self.update()

    def shutdown(self):
        self._generation += 1
        if self._cancel is not None:
            self._cancel.set()
        self._cancel = None

    def fit(self):
        self.view_start, self.view_end = 0.0, max(0.1, self.duration)
        self.fit_pitch()
        self.update()

    def pitch_bounds(self):
        span = max(1.0, (self.height() - 30.0 - self.PITCH_TOP) / self.note_height)
        return self.pitch_center - span / 2, self.pitch_center + span / 2

    def pitch_y_at(self, note):
        return (self.PITCH_TOP + self.height() - 30.0) / 2 + (
            self.pitch_center - note
        ) * self.note_height

    def fit_pitch(self):
        """Fit useful pitch detail, preserving readable lanes even with octave outliers."""
        self._pitch_auto_fit = True
        values = np.empty(0)
        if self.analysis is not None:
            voiced = np.isfinite(self.analysis.detected_midi) & (self.analysis.confidence >= 0.18)
            values = self.analysis.detected_midi[voiced]
            if len(self.guide) == len(voiced):
                values = np.concatenate((values, self.guide[voiced]))
                values = values[np.isfinite(values)]
        low, high = (54.0, 66.0)
        if len(values):
            low, high = np.percentile(values, (2, 98))
            low, high = float(low) - 2, float(high) + 2
        height = max(1.0, self.height() - 30.0 - self.PITCH_TOP)
        self.note_height = float(np.clip(height / max(12, high - low), self.MIN_NOTE_HEIGHT, 36))
        # When the whole range cannot fit, center the actual vocal rather than
        # forcing it against an edge because of an occasional octave jump.
        center = (
            np.median(values)
            if len(values) and high - low > height / self.note_height
            else (low + high) / 2
        )
        self.pitch_center = float(np.clip(center, 0, 127))
        self._sync_pitch_scroll()
        self.update()

    def zoom_pitch(self, factor):
        self._pitch_auto_fit = False
        self.note_height = float(np.clip(self.note_height * factor, self.MIN_NOTE_HEIGHT, 72))
        self._sync_pitch_scroll()
        self.update()

    def _scroll_pitch(self, value):
        self._pitch_auto_fit = False
        self.pitch_center = 127 - value / 10
        self.update()

    def _sync_pitch_scroll(self):
        self.pitch_scroll.blockSignals(True)
        self.pitch_scroll.setRange(0, 1270)
        self.pitch_scroll.setSingleStep(10)
        low, high = self.pitch_bounds()
        self.pitch_scroll.setPageStep(max(10, round((high - low) * 10)))
        self.pitch_scroll.setValue(round((127 - self.pitch_center) * 10))
        self.pitch_scroll.blockSignals(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.pitch_scroll.setGeometry(
            self.width() - 15,
            int(self.PITCH_TOP),
            14,
            max(1, int(self.height() - 30 - self.PITCH_TOP)),
        )
        if self._pitch_auto_fit:
            self.fit_pitch()
        else:
            self._sync_pitch_scroll()

    def clear_selection(self):
        self.selection = (0.0, 0.0)
        self.selectionChanged.emit(0.0, 0.0)
        self.update()

    def time_at(self, x):
        fraction = np.clip((x - 48) / max(1, self.width() - 70), 0, 1)
        return float(self.view_start + fraction * (self.view_end - self.view_start))

    def x_at(self, seconds):
        return 48 + (seconds - self.view_start) / (self.view_end - self.view_start) * (
            self.width() - 70
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.duration:
            self._anchor = self.time_at(event.position().x())
            self.cursor = self._anchor
            self.selection = (self._anchor, self._anchor)
            self.update()

    def mouseMoveEvent(self, event):
        if self._anchor is not None:
            current = self.time_at(event.position().x())
            self.selection = tuple(sorted((self._anchor, current)))
            self.selectionChanged.emit(*self.selection)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._anchor = None
            if self.selection[1] - self.selection[0] < 0.025:
                self.selection = (0.0, 0.0)
            self.selectionChanged.emit(*self.selection)
            self.update()

    def mouseDoubleClickEvent(self, event):
        self._anchor = None
        self.clear_selection()
        self.fit()

    def wheelEvent(self, event):
        if event.position().y() >= self.PITCH_TOP:
            self.pitch_scroll.setValue(self.pitch_scroll.value() - event.angleDelta().y() // 12)
            event.accept()
            return
        if not self.duration:
            event.ignore()
            return
        center = self.time_at(event.position().x())
        old_span = self.view_end - self.view_start
        span = float(
            np.clip(
                old_span * (0.8 if event.angleDelta().y() > 0 else 1.25),
                min(0.25, self.duration),
                self.duration,
            )
        )
        fraction = (center - self.view_start) / old_span
        self.view_start = float(np.clip(center - span * fraction, 0, max(0, self.duration - span)))
        self.view_end = self.view_start + span
        self.update()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.clear_selection()
        elif event.key() == Qt.Key_Home:
            self.fit()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), theme.q("bg"))
        p.setFont(theme.base_font(8))
        p.setPen(theme.q("dim"))
        p.drawText(12, 20, "WAVE")
        left, width = 48.0, max(1.0, self.width() - 70.0)
        bottom = self.height() - 30.0
        pitch_top = self.PITCH_TOP
        p.setClipRect(QRectF(left, 0, width, self.height()))
        for seconds in np.linspace(self.view_start, self.view_end, 7):
            x = self.x_at(seconds)
            p.setPen(theme.q("line"))
            p.drawLine(QPointF(x, 26), QPointF(x, bottom))
            p.setPen(theme.q("dim"))
            p.drawText(QRectF(x + 3, 0, 65, 24), Qt.AlignVCenter, f"{seconds:.2f}s")
        p.setPen(QPen(theme.q("dim"), 1))
        for index, peak in enumerate(self.peaks):
            x = self.x_at(index / max(1, len(self.peaks)) * self.duration)
            if left <= x <= left + width:
                height = min(1.0, float(peak)) * 31
                p.drawLine(QPointF(x, 62 - height), QPointF(x, 62 + height))
        p.setClipping(False)
        p.setPen(theme.q("line"))
        p.drawLine(QPointF(left, 100), QPointF(left + width, 100))
        low, high = self.pitch_bounds()
        p.setPen(theme.q("dim"))
        p.drawText(
            QRectF(left, 103, width, 20), Qt.AlignVCenter, "PITCH · scroll for higher / lower notes"
        )
        choices = set(int(note) for note in allowed_notes(self.settings))
        p.setClipRect(QRectF(0, pitch_top, left + width, bottom - pitch_top))
        for note in range(max(0, math.ceil(low)), min(127, math.floor(high)) + 1):
            y = self.pitch_y_at(note)
            if note in choices:
                p.fillRect(
                    QRectF(left, y - self.note_height / 2, width, self.note_height),
                    theme.q("accent", 12),
                )
            p.setPen(theme.q("dim2" if note % 12 == 0 else "line"))
            p.drawLine(
                QPointF(left, y + self.note_height / 2),
                QPointF(left + width, y + self.note_height / 2),
            )
            if pitch_top + 8 <= y <= bottom - 8:
                p.setPen(theme.q("fg" if note % 12 == 0 else "dim"))
                p.drawText(
                    QRectF(2, y - 8, 40, 16), Qt.AlignRight | Qt.AlignVCenter, note_name(note)
                )
        p.setClipRect(QRectF(left, pitch_top, width, bottom - pitch_top))
        if self.analysis is not None:
            for values, color, thickness in (
                (self.analysis.detected_midi, "dim", 1.5),
                (self.guide, "accent", 2.3),
            ):
                path = QPainterPath()
                connected = False
                previous = None
                for t, note, confidence in zip(
                    self.analysis.times, values, self.analysis.confidence, strict=True
                ):
                    if (
                        not np.isfinite(note)
                        or confidence < 0.18
                        or not self.view_start <= t <= self.view_end
                    ):
                        connected = False
                        continue
                    point = QPointF(self.x_at(t), self.pitch_y_at(note))
                    if connected and previous is not None and abs(note - previous) < 4:
                        path.lineTo(point)
                    else:
                        path.moveTo(point)
                    connected, previous = True, note
                p.setPen(QPen(theme.q(color), thickness))
                p.drawPath(path)
        p.setClipping(False)
        if self.selection[1] > self.selection[0]:
            start = max(left, self.x_at(self.selection[0]))
            end = min(left + width, self.x_at(self.selection[1]))
            if end > start:
                p.fillRect(QRectF(start, 26, end - start, bottom - 26), theme.q("accent", 35))
        p.setPen(theme.q("dim"))
        p.drawText(
            QRectF(left, bottom + 6, width, 22),
            Qt.AlignLeft,
            "Gray: recorded pitch     Color: correction guide · render to hear the result",
        )
        if self.message:
            p.fillRect(QRectF(left + 12, pitch_top + 15, width - 24, 62), theme.q("bg2", 235))
            p.setPen(theme.q("fg"))
            p.drawText(
                QRectF(left + 24, pitch_top + 20, width - 48, 52),
                Qt.AlignCenter | Qt.TextWordWrap,
                self.message,
            )
        p.end()
