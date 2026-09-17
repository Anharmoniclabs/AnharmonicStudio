"""First-party plugin controls inside Studio's instrument workflow."""

from dataclasses import asdict
import json
from pathlib import Path

import math
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDial,
    QFrame,
    QComboBox,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
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
from ..synth import PATCHES, PATCH_CATEGORIES

SPECS = json.loads((Path(__file__).resolve().parents[1] / "prism_parameters.json").read_text())
LOG_IDS = {"cutoff", "attack", "decay", "release", "lfo_rate"}
LOG_IDS |= {"b_" + key for key in LOG_IDS} | {"mod_rate", "trem_rate"}
PERFORMANCES = json.loads(
    (Path(__file__).resolve().parents[1] / "prism_performances.json").read_text()
)
CATALOG = {
    name: {"name": name, "category": PATCH_CATEGORIES.get(name, "Synth"), "patch": asdict(patch)}
    for name, patch in PATCHES.items()
    if not patch.sample_source
}
CATALOG.update({item["name"]: item for item in PERFORMANCES})
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
    painter.setPen(QPen(QColor("#d9b477"), 1.5))
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
        key = self.spec["id"].removeprefix("b_")
        if self.spec["step"]:
            return str(round(v))
        if key in {"cutoff", "lfo_rate", "mod_rate", "trem_rate"}:
            return f"{v / 1000:.1f} kHz" if v >= 1000 else f"{v:.1f} Hz"
        if key in {"attack", "decay", "release"}:
            return f"{v * 1000:.0f} ms" if v < 1 else f"{v:.2f} s"
        if key in {"detune", "lfo_pitch"}:
            return f"{v:.1f} ct"
        return f"{v * 100:.0f}%"

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#3b3b3b"), 5))
        r = QRectF(25, 8, 60, 60)
        p.drawArc(r, -225 * 16, -270 * 16)
        p.setPen(QPen(QColor("#d9b477" if not self.hasFocus() else "#f4ead8"), 5))
        p.drawArc(r, -225 * 16, int(-270 * 16 * self.value() / 10000))
        p.setBrush(QColor("#292929"))
        p.setPen(QPen(QColor("#53514b"), 1))
        p.drawEllipse(r.adjusted(8, 8, -8, -8))
        a = math.radians(135 + 270 * self.value() / 10000)
        p.setPen(QPen(QColor("#f4ead8"), 3))
        p.drawLine(
            QPointF(55 + math.cos(a) * 12, 38 + math.sin(a) * 12),
            QPointF(55 + math.cos(a) * 20, 38 + math.sin(a) * 20),
        )
        p.setPen(QColor("#eeeae2"))
        p.drawText(QRectF(0, 76, 110, 18), Qt.AlignCenter, self.text())
        p.setPen(QColor("#aaa69e"))
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
        self.setMinimumSize(240, 100)
        self.setMaximumHeight(110)
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
                str(12 + self.panel.edit_layer): max(
                    0, min(1, (event.position().x() - 16) / max(1, self.width() - 32))
                ),
                str(13 + self.panel.edit_layer): max(
                    0, min(1, 1 - (event.position().y() - 28) / max(1, self.height() - 48))
                ),
            }
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#18191a"))
        p.setPen(QPen(QColor("#383936"), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.setPen(QColor("#2b2d2e"))
        for x in range(16, self.width(), 32):
            p.drawLine(x, 28, x, self.height() - 16)
        for y in range(28, self.height(), 24):
            p.drawLine(16, y, self.width() - 16, y)
        p.setPen(QColor("#aaa69e"))
        p.drawText(
            16,
            18,
            "FILTER • drag to shape" if self.kind == "filter" else "LIVE OUTPUT • play a note",
        )
        p.setPen(QPen(QColor("#d9b477"), 2))
        if self.kind == "filter":
            x = 16 + self.panel.values[str(12 + self.panel.edit_layer)] * (self.width() - 32)
            y = 28 + (1 - self.panel.values[str(13 + self.panel.edit_layer)]) * (self.height() - 48)
            p.drawLine(QPointF(x, 28), QPointF(x, self.height() - 16))
            p.drawLine(QPointF(16, y), QPointF(self.width() - 16, y))
            p.setBrush(QColor("#d9b477"))
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


class WorkstationPlot(QWidget):
    def __init__(self, panel, kind):
        super().__init__()
        self.panel = panel
        self.kind = kind
        self.setMinimumSize(210, 90)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName(
            {
                "morph": "Tone and texture performance pad",
                "sequence": "Draw eight modulation steps",
                "envelope": "Drag envelope handles",
            }[kind]
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.panel.app.snapshot()
            self.handle = (
                0
                if event.position().x() < self.width() * 0.35
                else 1
                if event.position().x() < self.width() * 0.7
                else 2
            )
            self.edit(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.edit(event)

    def edit(self, event):
        x = max(0, min(1, (event.position().x() - 14) / max(1, self.width() - 28)))
        y = max(0, min(1, 1 - (event.position().y() - 26) / max(1, self.height() - 40)))
        layer = self.panel.edit_layer
        if self.kind == "morph":
            values = {"54": x, "57": y}
        elif self.kind == "sequence":
            values = {str(61 + min(7, int(x * 8))): y}
            if plain(SPECS[58], self.panel.values["58"]) == 0:
                values["58"] = normalized(SPECS[58], 0.5)
        elif self.handle == 0:
            values = {str(layer + 8): max(0, min(1, x / 0.35))}
        elif self.handle == 1:
            values = {str(layer + 9): max(0, min(1, (x - 0.35) / 0.35)), str(layer + 10): y}
        else:
            values = {str(layer + 11): max(0, min(1, (x - 0.7) / 0.3))}
        self.panel.change(values)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#18191a"))
        p.setPen(QPen(QColor("#383936"), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.setPen(QColor("#aaa69e"))
        p.drawText(
            14,
            18,
            {
                "morph": "MORPH • tone × texture",
                "sequence": "STEP MOTION • draw a rhythm",
                "envelope": "ENVELOPE • drag attack, decay, release",
            }[self.kind],
        )
        r = QRectF(14, 26, self.width() - 28, self.height() - 40)
        p.setPen(QColor("#2b2d2e"))
        for i in range(1, 8):
            x = r.left() + r.width() * i / 8
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        values = self.panel.values
        p.setPen(QPen(QColor("#c7a56c"), 2))
        if self.kind == "sequence":
            for i in range(8):
                h = values[str(61 + i)] * r.height()
                p.fillRect(
                    QRectF(
                        r.left() + r.width() * i / 8 + 3,
                        r.bottom() - h,
                        r.width() / 8 - 6,
                        max(2, h),
                    ),
                    QColor("#c7a56c"),
                )
        elif self.kind == "morph":
            x, y = r.left() + values["54"] * r.width(), r.bottom() - values["57"] * r.height()
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setBrush(QColor("#e2c795"))
            p.drawEllipse(QPointF(x, y), 6, 6)
        else:
            layer = self.panel.edit_layer
            points = [
                QPointF(r.left(), r.bottom()),
                QPointF(r.left() + r.width() * 0.35 * values[str(layer + 8)], r.top()),
                QPointF(
                    r.left() + r.width() * (0.35 + 0.35 * values[str(layer + 9)]),
                    r.bottom() - r.height() * values[str(layer + 10)],
                ),
                QPointF(r.left() + r.width() * (0.7 + 0.3 * values[str(layer + 11)]), r.bottom()),
            ]
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
                p.drawEllipse(point, 4, 4)
            p.drawPath(path)


class PrismControls(WindowClient, QWidget):
    """Embedded editor. Every gesture updates the existing plugin instance."""

    def __init__(self, window):
        super().__init__()
        self.app = window
        self.setObjectName("prismSurface")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            """
            #prismSurface { background: #202122; color: #eeeae2; }
            #prismSurface QWidget { color: #d8d5ce; font-size: 12px; }
            #prismSurface QLabel { background: transparent; }
            #prismSurface QPushButton {
                background: #2b2c2d; border: 1px solid #444440;
                border-radius: 4px; padding: 6px 10px; color: #d8d5ce;
            }
            #prismSurface QPushButton:hover { background: #363735; border-color: #81715a; }
            #prismSurface QPushButton:pressed { background: #484238; }
            #prismSurface QPushButton:checked {
                background: #494032; color: #f3dfbd; border-color: #b28f58;
            }
            #prismSurface QPushButton:focus, #prismSurface QLineEdit:focus,
            #prismSurface QComboBox:focus { border: 1px solid #d9b477; }
            #prismSurface QLineEdit, #prismSurface QComboBox {
                background: #191a1b; border: 1px solid #41423f;
                border-radius: 4px; padding: 6px 8px; color: #ddd9d1;
                selection-background-color: #64533a;
            }
            #prismSurface QListWidget {
                background: #191a1b; border: 1px solid #363735;
                border-radius: 4px; outline: 0;
            }
            #prismSurface QListWidget::item { padding: 6px 10px; border-bottom: 1px solid #272829; }
            #prismSurface QListWidget::item:selected { background: #433b2f; color: #f0d6ab; }
            #prismSurface QListWidget::item:hover { background: #30312f; }
            #prismSurface QTabWidget::pane { border: 1px solid #393a37; background: #202122; }
            #prismSurface QTabBar::tab {
                background: transparent; color: #a5a29a; padding: 9px 16px;
                border: none; border-bottom: 2px solid transparent;
            }
            #prismSurface QTabBar::tab:selected {
                background: #2b2b28; color: #e4c28d; border-bottom-color: #d9b477;
            }
            #prismSurface QTabBar::tab:hover { color: #eeeae2; background: #292a2a; }
            #prismSurface QScrollArea, #prismSurface QScrollArea > QWidget > QWidget {
                background: #202122;
            }
            #prismSurface QScrollBar:vertical { background: #202122; width: 7px; }
            #prismSurface QScrollBar::handle:vertical { background: #55544d; min-height: 24px; border-radius: 3px; }
            """
        )
        self.values = {str(i): normalized(s, s["default"]) for i, s in enumerate(SPECS)}
        self.edit_layer = 0
        self.knobs = {}
        self.selectors = {}
        self._stored = None
        self.camera_dialog = None
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 10)
        root.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("PRISM")
        title.setStyleSheet(
            "font-size: 26px; font-weight: 600; letter-spacing: 4px; color: #eeeae2; padding: 0px"
        )
        header.addWidget(title)
        header.addStretch()
        for label, callback in (
            ("Hand FX", self.show_camera),
            ("Store A", self.store),
            ("Swap A / B", self.swap),
            ("Save", self.save_sound),
            ("Open", self.open_sound),
            ("Reset", self.reset),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            header.addWidget(button)
        root.addLayout(header)
        self.sound_title = QLabel("Two-layer synthesizer")
        self.sound_title.setStyleSheet("color: #aaa69e; font-size: 12px; padding: 0px 0px 8px 0px")
        root.addWidget(self.sound_title)
        body = QHBoxLayout()
        body.setSpacing(16)
        library = QVBoxLayout()
        library.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sounds…")
        library.addWidget(self.search)
        self.category = QComboBox()
        self.category.addItems(
            ["All categories", *sorted({p["category"] for p in CATALOG.values()})]
        )
        self.category.currentTextChanged.connect(self.filter_presets)
        library.addWidget(self.category)
        self.favorites = set(window.settings.value("prism/favorites", []) or [])
        self.starred_only = QPushButton("★ Starred sounds")
        self.starred_only.setCheckable(True)
        self.starred_only.clicked.connect(self.filter_presets)
        library.addWidget(self.starred_only)
        self.presets = QListWidget()
        self.presets.setFixedWidth(220)
        self.presets.setSpacing(0)
        for name, item in CATALOG.items():
            row = QListWidgetItem(name + "\n" + item["category"])
            row.setData(Qt.UserRole, name)
            row.setSizeHint(QSize(206, 52))
            self.presets.addItem(row)
        self.presets.itemActivated.connect(lambda item: self.preset(item.data(Qt.UserRole)))
        self.search.textChanged.connect(self.filter_presets)
        library.addWidget(self.presets)
        for label, callback in (
            ("Load sound", self.load_selected),
            ("Load to layer A", lambda: self.load_layer(False)),
            ("Load to layer B", lambda: self.load_layer(True)),
            ("★ Star / unstar", self.star_selected),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            library.addWidget(button)
        body.addLayout(library)
        right = QVBoxLayout()
        right.setSpacing(10)
        macros = QHBoxLayout()
        for i in range(53, 58):
            knob = self.make_knob(i)
            macros.addWidget(knob, 1, Qt.AlignCenter)
        right.addLayout(macros)
        displays = QHBoxLayout()
        self.filter = PrismDisplay(self, "filter")
        self.filter.gesture.connect(self.app.snapshot)
        self.filter.changed.connect(self.change)
        self.scope = PrismDisplay(self, "scope")
        displays.addWidget(self.filter)
        displays.addWidget(self.scope)
        tabs = QTabWidget()
        self.pages = tabs
        performance = QWidget()
        performance_layout = QGridLayout(performance)
        performance_layout.setContentsMargins(8, 10, 8, 8)
        performance_layout.setSpacing(10)
        performance_layout.addLayout(displays, 0, 0, 1, 2)
        self.morph = WorkstationPlot(self, "morph")
        self.sequence = WorkstationPlot(self, "sequence")
        self.envelope = WorkstationPlot(self, "envelope")
        performance_layout.addWidget(self.morph, 1, 0)
        performance_layout.addWidget(self.sequence, 1, 1)
        performance_layout.addWidget(self.envelope, 2, 0, 1, 2)
        performance_scroll = QScrollArea()
        performance_scroll.setFrameShape(QFrame.NoFrame)
        performance_scroll.setWidgetResizable(True)
        performance_scroll.setWidget(performance)
        tabs.addTab(performance_scroll, "Perform")
        tabs.currentChanged.connect(self.page_changed)
        for title, indices in (
            ("Layer A", [*range(17), 20]),
            ("Layer B", list(range(32, 53))),
            ("Motion", [17, 18, 19, 21, 22, 23, 24, 25, 58, 59, 60, 69, 70, 71]),
            ("Effects", [26, 27, 28, 29, 30, 31, 72, 73, 74, 75]),
        ):
            page = QWidget()
            grid = QGridLayout(page)
            grid.setContentsMargins(16, 12, 16, 12)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(12)
            grid.setAlignment(Qt.AlignTop)
            row = col = 0
            for i in indices:
                spec = SPECS[i]
                choices = {
                    0: ["Saw", "Sine", "Triangle", "Pulse"],
                    1: ["Saw", "Sine", "Triangle", "Pulse"],
                    32: ["Saw", "Sine", "Triangle", "Pulse"],
                    33: ["Saw", "Sine", "Triangle", "Pulse"],
                    59: ["1/32", "1/8 T", "1/16", "1/8", "1/8 D", "1/4"],
                    60: ["Cutoff", "Detune", "Level", "Pan", "Osc blend"],
                    71: ["Cutoff", "Detune", "Level", "Pan", "Osc blend"],
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
                        if i in (0, 1, 32, 33):
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
                    knob = self.make_knob(i)
                    grid.addWidget(knob, row, col, Qt.AlignCenter)
                    col += 1
                    if col == 4:
                        row += 1
                        col = 0
            scroll = QScrollArea()
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            tabs.addTab(scroll, title)
        right.addWidget(tabs, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        hint = QLabel("Shift + drag  ·  Fine adjustment          Double-click  ·  Reset control")
        hint.setStyleSheet("color: #aaa69e; padding: 4px")
        root.addWidget(hint)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.scope.update)
        self.sync()

    def show_camera(self):
        from .prism_camera import PrismCameraDialog

        if self.camera_dialog is None:
            self.camera_dialog = PrismCameraDialog(self)
        self.camera_dialog.show()
        self.camera_dialog.raise_()

    def shutdown_camera(self):
        if self.camera_dialog is not None:
            self.camera_dialog.close()

    def make_knob(self, i):
        knob = PrismKnob(SPECS[i])
        knob.sliderPressed.connect(self.app.snapshot)
        knob.valueChanged.connect(lambda v, key=str(i), k=knob: self.knob_change(key, v, k))
        self.knobs[str(i)] = knob
        return knob

    def page_changed(self, index):
        if index in (1, 2):
            self.edit_layer = 32 if index == 2 else 0
        self.refresh()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def bridge(self):
        return self.app.engine.external_for(self.app.project.selected_instrument).instrument

    def _spec(self):
        instrument_id = self.app.project.selected_instrument
        if instrument_id is None:
            return self.app.project.plugins.get("instrument", {})
        instrument = next(
            (i for i in self.app.project.instruments if i.id == instrument_id), None
        )
        return (instrument.plugin if instrument else None) or {}

    def sync(self):
        saved = self._spec().get("parameters", {})
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
        self.morph.update()
        self.sequence.update()
        self.envelope.update()

    def change(self, values):
        bridge = self.bridge()
        if bridge is None or not hasattr(bridge, "set_parameters"):
            return
        bridge.set_parameters(values)
        self.values.update(values)
        instrument_id = self.app.project.selected_instrument
        if instrument_id is None:
            self.app.project.plugins["instrument"].setdefault("parameters", {}).update(values)
        else:
            instrument = next(
                (i for i in self.app.project.instruments if i.id == instrument_id), None
            )
            if instrument is not None and instrument.plugin is not None:
                instrument.plugin.setdefault("parameters", {}).update(values)
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

    def document_values(self, item):
        values = {str(i): normalized(s, s["default"]) for i, s in enumerate(SPECS)}
        patch = item["patch"]
        for i, spec in enumerate(SPECS[:21]):
            value = patch.get(spec["id"], spec["default"])
            if i < 2:
                value = WAVES.get(value, 0)
            values[str(i)] = normalized(spec, value)
        for i, spec in enumerate(SPECS[26:], 26):
            if spec["id"] in item.get("effects", {}):
                values[str(i)] = normalized(spec, item["effects"][spec["id"]])
        arp = item.get("arp", {})
        for i, value in (
            (21, int(arp.get("enabled", False))),
            (
                22,
                min(
                    range(6),
                    key=lambda n: abs(
                        [0.125, 1 / 3, 0.25, 0.5, 0.75, 1][n] - arp.get("rate_beats", 0.25)
                    ),
                ),
            ),
            (23, ["up", "down", "up/down", "random"].index(arp.get("mode", "up"))),
            (24, arp.get("octaves", 1)),
            (25, arp.get("gate", 0.72)),
        ):
            values[str(i)] = normalized(SPECS[i], value)
        return values

    def preset(self, name):
        self.app.snapshot()
        self.change(self.document_values(CATALOG[name]))
        self.sound_title.setText(name)

    def load_selected(self):
        item = self.presets.currentItem()
        if item:
            self.preset(item.data(Qt.UserRole))

    def load_layer(self, second):
        item = self.presets.currentItem()
        if item is None:
            return
        name = item.data(Qt.UserRole)
        first = self.document_values(CATALOG[name])
        offset = 32 if second else 0
        values = {str(i + offset): first[str(i)] for i in range(21)}
        if second and self.values["53"] == 0:
            values["53"] = 0.5
        self.app.snapshot()
        self.change(values)
        self.sound_title.setText(f"Layer {'B' if second else 'A'} · {name}")
        self.pages.setCurrentIndex(2 if second else 1)

    def star_selected(self):
        item = self.presets.currentItem()
        if item:
            name = item.data(Qt.UserRole)
            self.favorites.symmetric_difference_update({name})
            self.app.settings.setValue("prism/favorites", sorted(self.favorites))
            self.filter_presets()

    def filter_presets(self, *_):
        query = self.search.text().casefold()
        category = self.category.currentText()
        for i in range(self.presets.count()):
            item = self.presets.item(i)
            name = item.data(Qt.UserRole)
            match = query in (name + " " + CATALOG[name]["category"]).casefold()
            match &= category == "All categories" or category == CATALOG[name]["category"]
            match &= not self.starred_only.isChecked() or name in self.favorites
            item.setHidden(not match)

    def save_sound(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Prism sound", "My sound.prism.json", "Prism sound (*.prism.json)"
        )
        if not path:
            return
        patch = {}
        for i, spec in enumerate(SPECS[:21]):
            value = plain(spec, self.values[str(i)])
            patch[spec["id"]] = (
                list(WAVES)[round(value)] if i < 2 else round(value) if spec["step"] else value
            )
        arp = {
            "enabled": self.values["21"] > 0.5,
            "rate_beats": [0.125, 1 / 3, 0.25, 0.5, 0.75, 1][
                round(plain(SPECS[22], self.values["22"]))
            ],
            "mode": ["up", "down", "up/down", "random"][round(plain(SPECS[23], self.values["23"]))],
            "octaves": round(plain(SPECS[24], self.values["24"])),
            "gate": plain(SPECS[25], self.values["25"]),
        }
        doc = {
            "format": "anharmonic-prism",
            "version": 1,
            "name": self.sound_title.text(),
            "patch": patch,
            "arp": arp,
            "effects": {
                spec["id"]: plain(spec, self.values[str(i)])
                for i, spec in enumerate(SPECS[26:], 26)
            },
        }
        from ..library_journal import _atomic_json

        try:
            _atomic_json(Path(path), doc)
        except OSError as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

    def open_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Prism sound", "", "Prism sound (*.prism.json)"
        )
        if not path:
            return
        try:
            with Path(path).open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("Sound exceeds 1 MiB")
            item = json.loads(data)
            if item.get("format") != "anharmonic-prism" or item.get("version") != 1:
                raise ValueError("Choose a Prism sound")
            from ..prism import parse_sound

            parse_sound(data.decode())
            for value in item.get("effects", {}).values():
                if not isinstance(value, (float, int)) or not math.isfinite(value):
                    raise ValueError("Invalid effect parameter")
            values = self.document_values(item)
            self.app.snapshot()
            self.change(values)
            self.sound_title.setText(str(item.get("name", "User sound")))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Open failed", str(exc))

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
