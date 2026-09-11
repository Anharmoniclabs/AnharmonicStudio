"""Automation editing UI."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..music import AutomationLane, automation_targets
from . import theme


class AutomationCanvas(QWidget):
    """Small point editor shared by all automation targets."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self.drag_beat = None

    def point(self, beat, value):
        lane = self.panel.lane()
        lo, hi = lane.bounds if lane else (0.0, 1.0)
        width = max(1, self.width() - 18)
        height = max(1, self.height() - 18)
        span = max(1e-9, hi - lo)
        total = max(1e-9, self.panel.bars.value() * 4)
        return QPointF(9 + beat / total * width, 9 + (1 - (value - lo) / span) * height)

    def musical(self, position):
        lane = self.panel.lane()
        lo, hi = lane.bounds if lane else (0.0, 1.0)
        width = max(1, self.width() - 18)
        height = max(1, self.height() - 18)
        total = max(1e-9, self.panel.bars.value() * 4)
        beat = max(0.0, min(total, (position.x() - 9) / width * total))
        value = hi - max(0.0, min(height, position.y() - 9)) / height * (hi - lo)
        return round(beat, 3), value

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        beat, value = self.musical(event.position())
        lane = self.panel.lane(create=True)
        nearest = min(lane.points, key=lambda p: abs(p.beat - beat), default=None)
        tolerance = max(0.04, self.panel.bars.value() * 4 / max(80, self.width()))
        if nearest is not None and abs(nearest.beat - beat) <= tolerance:
            self.drag_beat = nearest.beat
            nearest.value = value
            nearest.beat = beat
            lane.points.sort(key=lambda p: p.beat)
        else:
            lane.put(beat, value)
            self.drag_beat = beat
        self.panel.show_values(beat, value)
        self.panel.changed()

    def mouseMoveEvent(self, event):
        if self.drag_beat is None or not (event.buttons() & Qt.LeftButton):
            return
        beat, value = self.musical(event.position())
        lane = self.panel.lane(create=True)
        lane.points = [p for p in lane.points if p.beat != self.drag_beat]
        lane.put(beat, value)
        self.drag_beat = beat
        self.panel.show_values(beat, value)
        self.panel.changed()

    def mouseReleaseEvent(self, event):
        self.drag_beat = None

    def _draw_lane_path(self, path, lane):
        """Draw the same interpolation shape that the engine evaluates."""
        first = lane.points[0]
        last = lane.points[-1]
        path.moveTo(self.point(0, first.value))
        if first.beat > 0:
            path.lineTo(self.point(first.beat, first.value))
        if lane.interpolation == "smooth":
            for left, right in zip(lane.points, lane.points[1:], strict=False):
                span = right.beat - left.beat
                for sample in range(1, 17):
                    beat = left.beat + span * sample / 16
                    value = float(lane.values([beat])[0])
                    path.lineTo(self.point(beat, value))
        else:
            previous = first.value
            for point in lane.points:
                if lane.interpolation == "step":
                    path.lineTo(self.point(point.beat, previous))
                path.lineTo(self.point(point.beat, point.value))
                previous = point.value
        path.lineTo(self.point(self.panel.bars.value() * 4, last.value))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.C["bg2"]))
        painter.setPen(QPen(QColor(theme.C["line"]), 1))
        rect = QRectF(9, 9, max(1, self.width() - 18), max(1, self.height() - 18))
        painter.drawRect(rect)
        for beat in range(0, self.panel.bars.value() * 4 + 1, 4):
            x = self.point(beat, 0).x()
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        lane = self.panel.lane()
        if lane and lane.points:
            path = QPainterPath()
            self._draw_lane_path(path, lane)
            painter.setPen(QPen(QColor(theme.C["accent"]), 2))
            painter.drawPath(path)
            painter.setBrush(QColor(theme.C["accent"]))
            painter.setPen(Qt.NoPen)
            for point in lane.points:
                center = self.point(point.beat, point.value)
                painter.drawEllipse(center, 4, 4)
        painter.setPen(QColor(theme.C["dim"]))
        painter.drawText(12, self.height() - 5, "click/drag to write points")


class AutomationPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.target = QComboBox()
        for item in automation_targets():
            self.target.addItem(item.replace("_", " ").title(), item)
        self.interpolation = QComboBox()
        self.interpolation.addItem("Linear", "linear")
        self.interpolation.addItem("Step", "step")
        self.interpolation.addItem("Smooth S-curve", "smooth")
        self.bars = QSpinBox()
        self.bars.setRange(1, 64)
        self.bars.setValue(8)
        clear = QPushButton("Clear lane")
        toolbar.addWidget(QLabel("Target"))
        toolbar.addWidget(self.target)
        toolbar.addWidget(QLabel("Curve"))
        toolbar.addWidget(self.interpolation)
        toolbar.addWidget(QLabel("Bars"))
        toolbar.addWidget(self.bars)
        toolbar.addStretch(1)
        toolbar.addWidget(clear)
        layout.addLayout(toolbar)
        self.canvas = AutomationCanvas(self)
        layout.addWidget(self.canvas)
        form = QFormLayout()
        self.beat = QDoubleSpinBox()
        self.beat.setRange(0, 256)
        self.beat.setDecimals(3)
        self.value = QDoubleSpinBox()
        self.value.setRange(-10000, 10000)
        self.value.setDecimals(4)
        form.addRow("Beat", self.beat)
        form.addRow("Value", self.value)
        layout.addLayout(form)
        self.target.currentIndexChanged.connect(self.sync)
        self.interpolation.currentIndexChanged.connect(self._curve_changed)
        self.bars.valueChanged.connect(self.canvas.update)
        clear.clicked.connect(self._clear)
        self.beat.valueChanged.connect(self._numeric_changed)
        self.value.valueChanged.connect(self._numeric_changed)
        self.sync()

    def lane(self, create=False):
        target = self.target.currentData()
        for lane in self.window.project.automation:
            if lane.target == target:
                return lane
        if create:
            lane = AutomationLane(target=target)
            self.window.project.automation.append(lane)
            return lane
        return None

    def show_values(self, beat, value):
        self.beat.blockSignals(True)
        self.value.blockSignals(True)
        self.beat.setValue(beat)
        self.value.setValue(value)
        self.beat.blockSignals(False)
        self.value.blockSignals(False)

    def changed(self):
        self.window._set_dirty(True)
        self.canvas.update()

    def sync(self):
        lane = self.lane()
        self.interpolation.blockSignals(True)
        self.interpolation.setCurrentIndex(
            max(0, self.interpolation.findData(lane.interpolation if lane else "linear"))
        )
        self.interpolation.blockSignals(False)
        lo, hi = lane.bounds if lane else (0.0, 1.0)
        self.value.blockSignals(True)
        self.value.setRange(lo, hi)
        self.value.blockSignals(False)
        self.canvas.update()

    def _curve_changed(self):
        lane = self.lane(create=True)
        lane.interpolation = self.interpolation.currentData()
        lane.validate()
        self.changed()

    def _clear(self):
        lane = self.lane()
        if lane is not None:
            self.window.project.automation.remove(lane)
            self.window._set_dirty(True)
        self.sync()

    def _numeric_changed(self):
        lane = self.lane(create=True)
        lane.put(self.beat.value(), self.value.value())
        self.changed()
