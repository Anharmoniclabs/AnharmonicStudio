"""Mastering workspace with calibrated offline meters and live visual scopes."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..mastering_analysis import measure_mastering, spectrum, stereo_field
from .theme import q


def _level(value: float, suffix: str) -> str:
    return f"−∞ {suffix}" if not math.isfinite(value) else f"{value:.2f} {suffix}"


class SpectrumCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.freqs = np.array([], dtype=np.float64)
        self.values = np.array([], dtype=np.float64)
        self.setMinimumHeight(190)
        self.setAccessibleName("Live frequency spectrum")

    def set_data(self, freqs, values):
        self.freqs = np.asarray(freqs, dtype=np.float64)
        self.values = np.asarray(values, dtype=np.float64)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), q("canvas"))
        rect = QRectF(42, 14, max(1, self.width() - 54), max(1, self.height() - 36))
        painter.setPen(q("line"))
        painter.drawRect(rect)
        for db in (-60, -48, -36, -24, -12, 0):
            y = rect.bottom() - (db + 60) / 60 * rect.height()
            painter.setPen(q("line"))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(q("dim"))
            painter.drawText(QRectF(0, y - 9, 37, 18), Qt.AlignRight | Qt.AlignVCenter, str(db))
        if len(self.freqs) and len(self.values):
            minimum = math.log10(20.0)
            maximum = math.log10(20_000.0)
            points = []
            for freq, magnitude in zip(self.freqs, self.values, strict=False):
                if freq < 20 or freq > 20_000:
                    continue
                db = max(-60.0, min(0.0, 20.0 * math.log10(max(float(magnitude), 1e-9))))
                x = (
                    rect.left()
                    + (math.log10(float(freq)) - minimum) / (maximum - minimum) * rect.width()
                )
                y = rect.bottom() - (db + 60.0) / 60.0 * rect.height()
                points.append(QPointF(x, y))
            painter.setPen(QPen(q("accent2"), 1.5))
            for left, right in zip(points, points[1:], strict=False):
                painter.drawLine(left, right)
        painter.setPen(q("dim"))
        painter.drawText(
            QRectF(rect.left(), rect.bottom() + 3, rect.width(), 18),
            Qt.AlignCenter,
            "20 Hz      100      1k      10k      20k",
        )
        painter.end()


class StereoFieldCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.audio = np.zeros((0, 2), dtype=np.float32)
        self.setMinimumHeight(190)
        self.setAccessibleName("Live stereo field scope")

    def set_audio(self, audio):
        data = np.asarray(audio, dtype=np.float32)
        if data.ndim == 2 and data.shape[1] >= 2:
            stride = max(1, len(data) // 1200)
            self.audio = data[::stride, :2].copy()
        else:
            self.audio = np.zeros((0, 2), dtype=np.float32)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), q("canvas"))
        side = min(self.width(), self.height()) - 24
        rect = QRectF((self.width() - side) / 2, 10, side, side)
        center = rect.center()
        painter.setPen(q("line"))
        painter.drawRect(rect)
        painter.drawLine(QPointF(center.x(), rect.top()), QPointF(center.x(), rect.bottom()))
        painter.drawLine(QPointF(rect.left(), center.y()), QPointF(rect.right(), center.y()))
        if len(self.audio):
            # 45-degree vectorscope: mono energy is vertical; side energy spreads horizontally.
            left = self.audio[:, 0].astype(np.float64)
            right = self.audio[:, 1].astype(np.float64)
            x = (left - right) * 0.5
            y = (left + right) * 0.5
            scale = side * 0.44
            painter.setPen(QPen(q("accent"), 1.0))
            for xv, yv in zip(x, y, strict=False):
                point = QPointF(center.x() + float(xv) * scale, center.y() - float(yv) * scale)
                if rect.contains(point):
                    painter.drawPoint(point)
        painter.setPen(q("dim"))
        painter.drawText(
            QRectF(rect.left(), rect.bottom() + 2, rect.width(), 18),
            Qt.AlignCenter,
            "SIDE ←   MONO   → SIDE",
        )
        painter.end()


class MasteringPanel(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        title = QLabel("MASTERING   /   DELIVERY ANALYSIS")
        title.setObjectName("workspaceTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Offline Analyze measures the rendered Song mix with K-weighted integrated LUFS, "
            "EBU-style loudness range and 4× reconstructed true peak. Live scopes read the "
            "post-master callback without blocking audio."
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)

        controls = QHBoxLayout()
        analyze = QPushButton("Analyze rendered mix")
        analyze.clicked.connect(self.analyze_mix)
        reset = QPushButton("Reset live scope")
        reset.clicked.connect(self.reset_live)
        controls.addWidget(analyze)
        controls.addWidget(reset)
        controls.addStretch(1)
        layout.addLayout(controls)

        metrics = QGridLayout()
        self.metric_labels = {}
        for index, (key, label) in enumerate(
            (
                ("lufs", "Integrated loudness"),
                ("lra", "Loudness range"),
                ("dbtp", "True peak"),
                ("sample", "Sample peak"),
                ("corr", "Stereo correlation"),
                ("width", "Side energy"),
            )
        ):
            metrics.addWidget(QLabel(label), index // 3 * 2, index % 3)
            value = QLabel("—")
            value.setObjectName("value")
            metrics.addWidget(value, index // 3 * 2 + 1, index % 3)
            self.metric_labels[key] = value
        layout.addLayout(metrics)

        scopes = QHBoxLayout()
        self.spectrum = SpectrumCanvas()
        self.field = StereoFieldCanvas()
        scopes.addWidget(self.spectrum, 1)
        scopes.addWidget(self.field, 1)
        layout.addLayout(scopes, 1)

        self.live = QLabel("Live: waiting for master output")
        self.live.setObjectName("hint")
        layout.addWidget(self.live)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_live)
        self.timer.start(150)

    def analyze_mix(self):
        try:
            audio = self.app.engine.render_offline(mode="song", tail=1.0)
            result = measure_mastering(audio, self.app.engine.sr)
        except Exception as exc:
            self.app.status.showMessage(f"Mastering analysis failed: {exc}", 6000)
            return
        self.metric_labels["lufs"].setText(_level(result.integrated_lufs, "LUFS"))
        self.metric_labels["lra"].setText(f"{result.loudness_range_lu:.2f} LU")
        self.metric_labels["dbtp"].setText(_level(result.true_peak_dbtp, "dBTP"))
        self.metric_labels["sample"].setText(_level(result.sample_peak_dbfs, "dBFS"))
        self.metric_labels["corr"].setText(f"{result.correlation:+.3f}")
        self.metric_labels["width"].setText(f"{result.side_percent:.1f}%")
        self.app.status.showMessage("Rendered mix mastering analysis complete", 4000)

    def reset_live(self):
        tap = getattr(self.app.engine, "mastering_tap", None)
        if tap is not None:
            tap.reset()
        self.spectrum.set_data([], [])
        self.field.set_audio([])
        self.live.setText("Live: reset")

    def refresh_live(self):
        if not self.isVisible():
            return
        tap = getattr(self.app.engine, "mastering_tap", None)
        if tap is None:
            self.live.setText("Live analysis tap unavailable")
            return
        data = tap.snapshot(min(tap.sample_rate * 2, tap.capacity))
        if not len(data):
            self.live.setText("Live: waiting for master output")
            return
        freqs, values = spectrum(data, tap.sample_rate, bins=420)
        field = stereo_field(data)
        sample_peak = float(np.max(np.abs(data), initial=0.0))
        peak_db = -math.inf if sample_peak <= 0 else 20.0 * math.log10(sample_peak)
        self.spectrum.set_data(freqs, values)
        self.field.set_audio(data)
        self.live.setText(
            f"Live sample peak {_level(peak_db, 'dBFS')}   •   "
            f"correlation {field['correlation']:+.3f}   •   side {field['side_percent']:.1f}%"
        )


def attach_mastering_workspace(window, controller=None):
    if getattr(window, "mastering_panel", None) is not None:
        return window.mastering_panel
    panel = MasteringPanel(window)
    window.mastering_panel = panel
    window.tabs.addTab(panel, "Mastering")
    window.tabs.setTabToolTip(
        window.tabs.indexOf(panel), "LUFS, LRA, true peak, spectrum and stereo-field analysis"
    )
    return panel
