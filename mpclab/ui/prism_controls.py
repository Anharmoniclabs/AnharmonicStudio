"""First-party plugin controls inside Studio's instrument workflow."""

from dataclasses import asdict
import json
from pathlib import Path

import math
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDial,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from .window_client import WindowClient
from ..synth import PATCHES

SPECS = json.loads((Path(__file__).resolve().parents[1] / "prism_parameters.json").read_text())
LOG_IDS = {"cutoff", "attack", "decay", "release", "lfo_rate"}
WAVES = {"saw": 0, "sine": 1, "triangle": 2, "square": 3}


def normalized(spec, value):
    proportion = min(1, max(0, (value - spec["low"]) / (spec["high"] - spec["low"])))
    return proportion ** (0.3 if spec["id"] in LOG_IDS else 1)


def plain(spec, value):
    return spec["low"] + min(1, max(0, value)) ** (1 / 0.3 if spec["id"] in LOG_IDS else 1) * (
        spec["high"] - spec["low"]
    )


def patch_parameters(patch):
    data = asdict(patch)
    return {
        str(i): normalized(spec, WAVES[data[spec["id"]]] if i < 2 else data[spec["id"]])
        for i, spec in enumerate(SPECS[:21])
    }


def waveform_icon(shape):
    pixmap = QPixmap(46, 22)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#89ebd6"), 1.5))
    path = QPainterPath()
    for x in range(44):
        phase = (x / 22) % 1
        value = (
            2 * phase - 1,
            math.sin(phase * 2 * math.pi),
            1 - 4 * abs(phase - 0.5),
            1 if phase < 0.5 else -1,
        )[shape]
        point = QPointF(x + 1, 11 - value * 8)
        if x:
            path.lineTo(point)
        else:
            path.moveTo(point)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class PrismKnob(QDial):
    """Accessible rotary control with a human-readable value and drag gestures."""

    def __init__(self, spec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.setRange(0, 10000)
        self.setSingleStep(25)
        self.setPageStep(500)
        self.setFixedSize(110, 116)
        self.setAccessibleName(spec["label"])
        self.setToolTip(
            f"{spec['label']} · drag vertically · Shift for fine control · double-click to reset"
        )
        self.setFocusPolicy(Qt.StrongFocus)

    def text(self):
        v = plain(self.spec, self.value() / 10000)
        key = self.spec["id"]
        if self.spec["step"]:
            return str(round(v))
        if key in {"cutoff", "lfo_rate"}:
            return f"{v / 1000:.1f} kHz" if v >= 1000 else f"{v:.1f} Hz"
        if key in {"attack", "decay", "release"}:
            return f"{v * 1000:.0f} ms" if v < 1 else f"{v:.2f} s"
        if key in {"detune", "lfo_pitch"}:
            return f"{v:.1f} ct"
        return f"{v * 100:.0f}%"

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#263c4a"), 5))
        r = QRectF(25, 8, 60, 60)
        p.drawArc(r, -225 * 16, -270 * 16)
        p.setPen(QPen(QColor("#69e2cb" if not self.hasFocus() else "#d3b0ff"), 5))
        p.drawArc(r, -225 * 16, int(-270 * 16 * self.value() / 10000))
        p.setBrush(QColor("#203444"))
        p.setPen(QPen(QColor("#496075"), 1))
        p.drawEllipse(r.adjusted(8, 8, -8, -8))
        a = math.radians(135 + 270 * self.value() / 10000)
        p.setPen(QPen(QColor("#e1fff8"), 3))
        p.drawLine(
            QPointF(55 + math.cos(a) * 12, 38 + math.sin(a) * 12),
            QPointF(55 + math.cos(a) * 20, 38 + math.sin(a) * 20),
        )
        p.setPen(QColor("#f1f5ff"))
        p.drawText(QRectF(0, 76, 110, 18), Qt.AlignCenter, self.text())
        p.setPen(QColor("#9cafc1"))
        p.drawText(QRectF(0, 95, 110, 20), Qt.AlignCenter, self.spec["label"])

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setFocus()
            self._drag = (event.position().y(), self.value())
            self.setSliderDown(True)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            y, value = self._drag
            scale = 5 if event.modifiers() & Qt.ShiftModifier else 60
            self.setValue(round(value + (y - event.position().y()) * scale))

    def mouseReleaseEvent(self, event):
        self.setSliderDown(False)

    def mouseDoubleClickEvent(self, event):
        self.setValue(round(normalized(self.spec, self.spec["default"]) * 10000))


class PrismDisplay(QWidget):
    changed = Signal(dict)
    gesture = Signal()

    def __init__(self, panel, kind):
        super().__init__()
        self.panel = panel
        self.kind = kind
        self.setMinimumSize(240, 140)
        self.setAccessibleName(
            "Cutoff and resonance XY pad" if kind == "filter" else "Live plugin output waveform"
        )
        if kind == "filter":
            self.setCursor(Qt.CrossCursor)
            self.setToolTip("Drag left/right for cutoff; up/down for resonance")

    def mousePressEvent(self, event):
        if self.kind == "filter" and event.button() == Qt.LeftButton:
            self.gesture.emit()
            self._move(event)

    def mouseMoveEvent(self, event):
        if self.kind == "filter" and event.buttons() & Qt.LeftButton:
            self._move(event)

    def _move(self, event):
        self.changed.emit(
            {
                "12": max(0, min(1, (event.position().x() - 16) / max(1, self.width() - 32))),
                "13": max(0, min(1, 1 - (event.position().y() - 28) / max(1, self.height() - 48))),
            }
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#091720"))
        p.setPen(QColor("#1d3343"))
        for x in range(16, self.width(), 32):
            p.drawLine(x, 28, x, self.height() - 16)
        for y in range(28, self.height(), 24):
            p.drawLine(16, y, self.width() - 16, y)
        p.setPen(QColor("#9cafc1"))
        p.drawText(
            16,
            18,
            "FILTER • drag to shape" if self.kind == "filter" else "LIVE OUTPUT • play a note",
        )
        p.setPen(QPen(QColor("#69e2cb"), 2))
        if self.kind == "filter":
            x = 16 + self.panel.values["12"] * (self.width() - 32)
            y = 28 + (1 - self.panel.values["13"]) * (self.height() - 48)
            p.drawLine(QPointF(x, 28), QPointF(x, self.height() - 16))
            p.drawLine(QPointF(16, y), QPointF(self.width() - 16, y))
            p.setBrush(QColor("#69e2cb"))
            p.drawEllipse(QPointF(x, y), 6, 6)
        else:
            bridge = self.panel.bridge()
            data = getattr(bridge, "scope", ())
            path = QPainterPath()
            for i, sample in enumerate(data):
                point = QPointF(
                    16 + i / max(1, len(data) - 1) * (self.width() - 32),
                    self.height() / 2 - float(sample[0]) * (self.height() - 40) / 2,
                )
                if i == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            p.drawPath(path)


class PrismControls(WindowClient, QWidget):
    """Embedded editor. Every gesture updates the existing plugin instance."""

    def __init__(self, window):
        super().__init__()
        self.app = window
        self.setObjectName("prismSurface")
        self.setStyleSheet(
            "#prismSurface { background: #101e2b; } QPushButton:checked { background: #336b69; color: #effffb; border: 1px solid #69e2cb; }"
        )
        self.values = {str(i): normalized(s, s["default"]) for i, s in enumerate(SPECS)}
        self.knobs = {}
        self.selectors = {}
        self._stored = None
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("PRISM  /  SOUND LAB")
        title.setStyleSheet("font-size: 21px; font-weight: bold; color: #84e4cf; padding: 8px")
        header.addWidget(title)
        header.addStretch()
        for label, callback in (
            ("Store A", self.store),
            ("Swap A / B", self.swap),
            ("Reset tone", self.reset),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            header.addWidget(button)
        root.addLayout(header)
        body = QHBoxLayout()
        library = QVBoxLayout()
        search = QLineEdit()
        search.setPlaceholderText("Find a sound…")
        library.addWidget(search)
        self.presets = QListWidget()
        self.presets.setFixedWidth(180)
        self.presets.addItems([n for n, p in PATCHES.items() if not p.sample_source])
        self.presets.itemClicked.connect(lambda item: self.preset(item.text()))
        search.textChanged.connect(self.filter_presets)
        library.addWidget(self.presets)
        body.addLayout(library)
        right = QVBoxLayout()
        displays = QHBoxLayout()
        self.filter = PrismDisplay(self, "filter")
        self.filter.gesture.connect(self.app.snapshot)
        self.filter.changed.connect(self.change)
        self.scope = PrismDisplay(self, "scope")
        displays.addWidget(self.filter)
        displays.addWidget(self.scope)
        right.addLayout(displays)
        tabs = QTabWidget()
        for title, indices in (
            ("Oscillators", [0, 1, 2, 3, 4, 5, 6, 7, 16, 20]),
            ("Filter & envelope", [8, 9, 10, 11, 12, 13, 14, 15]),
            ("Motion & arp", [17, 18, 19, 21, 22, 23, 24, 25]),
            ("Effects", [26, 27, 28, 29, 30, 31]),
        ):
            page = QWidget()
            grid = QGridLayout(page)
            row = col = 0
            for i in indices:
                spec = SPECS[i]
                choices = {
                    0: ["Saw", "Sine", "Triangle", "Pulse"],
                    1: ["Saw", "Sine", "Triangle", "Pulse"],
                    21: ["Off", "On"],
                    22: ["1/32", "1/8 T", "1/16", "1/8", "1/8 D", "1/4"],
                    23: ["Up", "Down", "Up / down", "Random"],
                    28: ["1/32", "1/8 T", "1/16", "1/8", "1/8 D", "1/4"],
                }.get(i)
                if choices:
                    if col:
                        row += 1
                        col = 0
                    selector = QHBoxLayout()
                    selector.addWidget(QLabel(spec["label"]))
                    buttons = []
                    for v, label in enumerate(choices):
                        button = QPushButton(label)
                        if i in (0, 1):
                            button.setIcon(waveform_icon(v))
                            button.setIconSize(QSize(46, 22))
                        button.setCheckable(True)
                        button.clicked.connect(
                            lambda checked=False, key=str(i), value=v: self.discrete(key, value)
                        )
                        selector.addWidget(button)
                        buttons.append(button)
                    self.selectors[str(i)] = buttons
                    grid.addLayout(selector, row, 0, 1, 4)
                    row += 1
                else:
                    knob = PrismKnob(spec)
                    knob.sliderPressed.connect(self.app.snapshot)
                    knob.valueChanged.connect(
                        lambda v, key=str(i), k=knob: self.knob_change(key, v, k)
                    )
                    self.knobs[str(i)] = knob
                    grid.addWidget(knob, row, col, Qt.AlignCenter)
                    col += 1
                    if col == 4:
                        row += 1
                        col = 0
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            tabs.addTab(scroll, title)
        right.addWidget(tabs, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        hint = QLabel(
            "Drag knobs to shape your sound • Shift = fine • double-click = reset • changes are live"
        )
        hint.setStyleSheet("color: #9cafc1; padding: 4px")
        root.addWidget(hint)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.scope.update)
        self.sync()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def bridge(self):
        return getattr(self.app.engine.external, "instrument", None)

    def sync(self):
        saved = self.app.project.plugins.get("instrument", {}).get("parameters", {})
        info = getattr(self.bridge(), "info", {}).get("parameters", {})
        self.values = {
            str(i): saved.get(
                str(i), info.get(str(i), {}).get("value", normalized(s, s["default"]))
            )
            for i, s in enumerate(SPECS)
        }
        self.refresh()

    def refresh(self):
        for key, knob in self.knobs.items():
            knob.blockSignals(True)
            knob.setValue(round(self.values[key] * 10000))
            knob.blockSignals(False)
            knob.update()
        for key, buttons in self.selectors.items():
            selected = round(plain(SPECS[int(key)], self.values[key]))
            for i, button in enumerate(buttons):
                button.setChecked(i == selected)
        self.filter.update()

    def change(self, values):
        bridge = self.bridge()
        if bridge is None or not hasattr(bridge, "set_parameters"):
            return
        bridge.set_parameters(values)
        self.values.update(values)
        self.app.project.plugins["instrument"].setdefault("parameters", {}).update(values)
        self.app._set_dirty(True)
        self.refresh()

    def knob_change(self, key, value, knob):
        if not knob.isSliderDown():
            self.app.snapshot()
        spec = SPECS[int(key)]
        value = value / 10000
        if spec["step"]:
            value = normalized(spec, round(plain(spec, value)))
        self.change({key: value})

    def discrete(self, key, value):
        self.app.snapshot()
        self.change({key: normalized(SPECS[int(key)], value)})

    def preset(self, name):
        self.app.snapshot()
        values = {str(i): normalized(s, s["default"]) for i, s in enumerate(SPECS)}
        values.update(patch_parameters(PATCHES[name]))
        self.change(values)

    def filter_presets(self, text):
        for i in range(self.presets.count()):
            item = self.presets.item(i)
            item.setHidden(text.casefold() not in item.text().casefold())

    def store(self):
        self._stored = dict(self.values)

    def swap(self):
        if self._stored is not None:
            self.app.snapshot()
            previous = dict(self.values)
            self.change(self._stored)
            self._stored = previous

    def reset(self):
        self.app.snapshot()
        self.change({str(i): normalized(s, s["default"]) for i, s in enumerate(SPECS)})
