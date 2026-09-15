"""A compact, consistently rendered hue-wheel customization dialog."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QSize, QPointF, Signal
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QDialogButtonBox,
)

from .theme import q, stylesheet


class ColorWheel(QWidget):
    colorChanged = Signal(QColor)

    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self.hue = color.hsvHue() if color.hsvHue() >= 0 else 0
        self.setMinimumSize(190, 190)
        self.setCursor(Qt.CrossCursor)

    def sizeHint(self) -> QSize:
        return QSize(220, 220)

    def _choose(self, pos) -> None:
        centre = self.rect().center()
        dx, dy = pos.x() - centre.x(), pos.y() - centre.y()
        if dx == 0 and dy == 0:
            return
        self.hue = int(math.degrees(math.atan2(-dy, dx))) % 360
        self.colorChanged.emit(QColor.fromHsv(self.hue, 255, 255))
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._choose(ev.position())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.LeftButton:
            self._choose(ev.position())

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        centre = QPointF(self.width() / 2, self.height() / 2)
        radius = max(20.0, min(self.width(), self.height()) / 2 - 10)
        grad = QConicalGradient(centre, 0)
        for degree in range(0, 361, 15):
            grad.setColorAt(degree / 360.0, QColor.fromHsv((360 - degree) % 360, 255, 255))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(centre, radius, radius)
        p.setBrush(q("bg2"))
        p.drawEllipse(centre, radius - 34, radius - 34)

        angle = math.radians(-self.hue)
        marker_radius = radius - 17
        marker = QPointF(
            centre.x() + math.cos(angle) * marker_radius,
            centre.y() + math.sin(angle) * marker_radius,
        )
        p.setBrush(QColor.fromHsv(self.hue, 255, 255))
        p.setPen(QPen(q("fg"), 3))
        p.drawEllipse(marker, 7, 7)


class TonePickerDialog(QDialog):
    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose your tone")
        self.setStyleSheet(stylesheet())
        hue = color.hsvHue() if color.hsvHue() >= 0 else 0
        self._hue = hue

        lay = QVBoxLayout(self)
        title = QLabel("SELECT HUE")
        title.setObjectName("header")
        lay.addWidget(title)
        self.wheel = ColorWheel(color)
        self.wheel.colorChanged.connect(self._hue_changed)
        lay.addWidget(self.wheel, 0, Qt.AlignHCenter)

        self.saturation = self._slider(color.hsvSaturation())
        self.value = self._slider(color.value())
        for label, slider in (("SATURATION", self.saturation), ("BRIGHTNESS", self.value)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(slider, 1)
            lay.addLayout(row)

        self.preview = QLabel()
        self.preview.setFixedHeight(34)
        self.preview.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.preview)
        self.saturation.valueChanged.connect(self._update_preview)
        self.value.valueChanged.connect(self._update_preview)
        self._update_preview()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @staticmethod
    def _slider(value: int) -> QSlider:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 255)
        slider.setValue(value)
        return slider

    def _hue_changed(self, color: QColor) -> None:
        self._hue = color.hsvHue()
        self._update_preview()

    def selectedColor(self) -> QColor:
        return QColor.fromHsv(self._hue, self.saturation.value(), self.value.value())

    def _update_preview(self) -> None:
        color = self.selectedColor()
        ink = "#101114" if color.lightnessF() > 0.58 else "#ffffff"
        self.preview.setText(color.name().upper())
        self.preview.setStyleSheet(
            f"background:{color.name()};color:{ink};border-radius:5px;font-weight:700"
        )
