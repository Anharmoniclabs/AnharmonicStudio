"""Arrangement fader automation with editable points and explicit bypass."""

from .window_client import WindowClient

from PySide6.QtCore import Qt, QRectF, QPointF, QTimer
from PySide6.QtGui import QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
)

from ..music import AutomationLane, automation_targets, target_range
from .theme import q
from .editor_tools import editor_bar


class AutomationCanvas(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.drag_beat = None
        self.setMinimumHeight(210)
        self.setAccessibleName("Automation envelope")
        self.setToolTip("Click to add a point • drag to move • right-click a point to remove")

    def plot(self):
        return QRectF(58, 32, max(1, self.width() - 80), max(1, self.height() - 66))

    def point(self, beat, value):
        r = self.plot()
        lo, hi = target_range(self.panel.target.currentData())
        return QPointF(
            r.left() + beat / (self.panel.bars.value() * 4) * r.width(),
            r.bottom() - (value - lo) / (hi - lo) * r.height(),
        )

    def musical(self, pos):
        r = self.plot()
        lo, hi = target_range(self.panel.target.currentData())
        beat = (
            round(max(0, min(1, (pos.x() - r.left()) / r.width())) * self.panel.bars.value() * 16)
            / 4
        )
        value = lo + max(0, min(1, (r.bottom() - pos.y()) / r.height())) * (hi - lo)
        return beat, value

    def nearest(self, pos):
        lane = self.panel.lane()
        if lane:
            return next(
                (
                    p
                    for p in lane.points
                    if (self.point(p.beat, p.value) - pos).manhattanLength() < 14
                ),
                None,
            )
        return None

    def mousePressEvent(self, event):
        if not self.plot().adjusted(-7, -7, 7, 7).contains(event.position()):
            return
        point = self.nearest(event.position())
        if event.button() == Qt.RightButton:
            if point:
                self.panel.app.snapshot()
                lane = self.panel.lane()
                lane.points = [p for p in lane.points if p.beat != point.beat]
                self.panel.changed()
            return
        if event.button() != Qt.LeftButton:
            return
        self.panel.app.snapshot()
        beat, value = (point.beat, point.value) if point else self.musical(event.position())
        self.panel.lane(create=True).put(beat, value)
        self.drag_beat = beat
        self.panel.show_values(beat, value)
        self.panel.changed()

    def mouseMoveEvent(self, event):
        if self.drag_beat is None:
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
            for left, right in zip(lane.points, lane.points[1:]):
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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), q("canvas"))
        r = self.plot()
        lo, hi = target_range(self.panel.target.currentData())
        for i in range(5):
            value = lo + i * (hi - lo) / 4
            y = self.point(0, value).y()
            p.setPen(q("line"))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(q("dim"))
            p.drawText(QRectF(0, y - 10, 48, 20), Qt.AlignRight | Qt.AlignVCenter, f"{value:.2f}")
        stride = max(1, self.panel.bars.value() // max(1, int(r.width() / 65)))
        for bar in range(0, self.panel.bars.value() + 1, stride):
            x = self.point(bar * 4, lo).x()
            p.setPen(q("line"))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(q("fg"))
            p.drawText(QRectF(x + 4, 5, 54, 20), Qt.AlignVCenter, str(bar + 1))
        lane = self.panel.lane()
        p.setClipRect(r.adjusted(-6, -6, 6, 6))
        if lane and lane.points:
            path = QPainterPath()
            self._draw_lane_path(path, lane)
            p.setPen(QPen(q("accent2") if lane.enabled else q("dim"), 2.5))
            p.drawPath(path)
            p.setBrush(q("accent"))
            for point in lane.points:
                p.drawEllipse(self.point(point.beat, point.value), 5, 5)
        else:
            p.setPen(q("dim"))
            p.drawText(
                r,
                Qt.AlignCenter,
                "Click to draw an envelope\nValues follow the arrangement in Song mode",
            )
        if self.panel.app.engine.mode == "song":
            x = self.point(self.panel.app.engine.beat, lo).x()
            p.setPen(QPen(q("ok"), 2))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        p.end()


class AutomationPanel(WindowClient, QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        title = QLabel("AUTOMATION   /   ARRANGEMENT")
        title.setObjectName("workspaceTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Shape the mix over time. Envelopes override their fader in Song mode; bypass a lane to return to manual control."
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)
        top = QHBoxLayout()
        self.target = QComboBox()
        for target in automation_targets():
            label = (
                "Master • level"
                if target == "master"
                else f"Track {int(target.split(':')[1]) + 1} • {target.split(':')[2]}"
            )
            self.target.addItem(label, target)
        top.addWidget(self.target)
        self.enabled = QCheckBox("Read automation")
        self.enabled.setChecked(True)
        self.enabled.toggled.connect(self.set_enabled)
        top.addWidget(self.enabled)
        self.interpolation = QComboBox()
        self.interpolation.addItem("Linear ramp", "linear")
        self.interpolation.addItem("Smooth S-curve", "smooth")
        self.interpolation.addItem("Hold / step", "step")
        self.interpolation.currentIndexChanged.connect(self.set_interpolation)
        top.addWidget(self.interpolation)
        top.addStretch()
        top.addWidget(QLabel("View bars"))
        self.bars = QSpinBox()
        self.bars.setRange(1, 4096)
        self.bars.setValue(16)
        top.addWidget(self.bars)
        layout.addWidget(editor_bar(top))
        self.canvas = AutomationCanvas(self)
        layout.addWidget(self.canvas, 1)
        bottom = QHBoxLayout()
        self.beat = QDoubleSpinBox()
        self.beat.setRange(0, 1_000_000)
        self.beat.setDecimals(3)
        self.beat.setSingleStep(0.25)
        self.value = QDoubleSpinBox()
        self.value.setDecimals(3)
        self.value.setSingleStep(0.05)
        for title, box in (("Beat (from 0)", self.beat), ("Value", self.value)):
            box.setAccessibleName(title)
            bottom.addWidget(QLabel(title))
            bottom.addWidget(box)
        add = QPushButton("Set point")
        add.clicked.connect(self.set_point)
        bottom.addWidget(add)
        erase = QPushButton("Remove point")
        erase.clicked.connect(self.remove_point)
        bottom.addWidget(erase)
        clear = QPushButton("Clear lane")
        clear.clicked.connect(self.clear_lane)
        bottom.addWidget(clear)
        bottom.addStretch()
        layout.addWidget(editor_bar(bottom))
        self.status = QLabel()
        self.status.setObjectName("hint")
        layout.addWidget(self.status)
        self.target.currentIndexChanged.connect(self.sync)
        self.bars.valueChanged.connect(self.canvas.update)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.canvas.update() if self.isVisible() else None)
        self.timer.start(50)
        self.sync()

    def lane(self, create=False):
        target = self.target.currentData()
        lane = next((a for a in self.app.project.automation if a.target == target), None)
        if lane is None and create:
            lane = AutomationLane(
                target,
                enabled=self.enabled.isChecked(),
                interpolation=self.interpolation.currentData(),
            )
            self.app.project.automation = self.app.project.automation + [lane]
        return lane

    def sync(self):
        lane = self.lane()
        self.canvas.drag_beat = None
        self.enabled.blockSignals(True)
        self.enabled.setChecked(lane.enabled if lane else True)
        self.enabled.blockSignals(False)
        self.interpolation.blockSignals(True)
        mode = lane.interpolation if lane else "linear"
        index = self.interpolation.findData(mode)
        self.interpolation.setCurrentIndex(max(0, index))
        self.interpolation.blockSignals(False)
        self.value.setRange(*target_range(self.target.currentData()))
        self.value.setValue(0 if self.target.currentData().endswith(":pan") else 1)
        self.update_status()
        self.canvas.update()

    def show_values(self, beat, value):
        self.beat.setValue(beat)
        self.value.setValue(value)

    def update_status(self):
        lane = self.lane()
        self.status.setText(
            f"{len(lane.points) if lane else 0} points  •  "
            "Click / drag to draw  •  Right-click to remove  •  Ctrl+Z to undo"
        )

    def changed(self):
        self.app._set_dirty(True)
        self.canvas.update()
        self.update_status()

    def set_point(self):
        self.app.snapshot()
        self.lane(create=True).put(self.beat.value(), self.value.value())
        self.changed()

    def remove_point(self):
        lane = self.lane()
        if lane and any(abs(p.beat - self.beat.value()) < 0.0005 for p in lane.points):
            self.app.snapshot()
            lane.points = [p for p in lane.points if abs(p.beat - self.beat.value()) >= 0.0005]
            self.changed()

    def set_enabled(self, enabled):
        self.app.snapshot()
        self.lane(create=True).enabled = enabled
        self.changed()

    def set_interpolation(self):
        self.app.snapshot()
        self.lane(create=True).interpolation = self.interpolation.currentData()
        self.changed()

    def clear_lane(self):
        lane = self.lane()
        if lane:
            self.app.snapshot()
            self.app.project.automation = [a for a in self.app.project.automation if a is not lane]
            self.sync()
            self.changed()
