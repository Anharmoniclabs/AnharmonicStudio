"""Analog instrument controls, patch browser, arpeggiator, and piano keyboard."""

from __future__ import annotations

import math
import threading

from .window_client import WindowClient, emit_if_alive

from PySide6.QtCore import Qt, QRectF, Signal, QTimer, QPointF, QSize, QEvent
from PySide6.QtGui import QPainter, QPen, QPolygonF, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QListView,
    QLineEdit,
    QSplitter,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..synth import PATCHES, PATCH_CATEGORIES, PATCH_DESCRIPTIONS, WAVEFORMS, patch_copy
from .. import orchestra
from .keymap import MUSICAL_OFFSET_LABELS
from .theme import q


BLACK_NOTES = {1, 3, 6, 8, 10}
NOTE_NAMES = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")
PATCH_CHARACTER = {
    "Midnight Brass": "BROAD · WARM · EXPRESSIVE",
    "Copper Pluck": "PERCUSSIVE · WOOD + METAL",
    "Velvet Poly": "WIDE · SOFT · DETUNED",
    "Acid Orchard": "RESONANT · BITING · ELASTIC",
    "Dust Choir": "AGED · AIRY · SLOW MOTION",
    "Neon Sub": "DEEP · FOCUSED · SATURATED",
    "Glass Current": "BRIGHT · LIQUID · ELECTRIC",
    "Broken Carousel": "UNSTABLE · PLAYFUL · WORN",
    "Solar Strings": "OPEN · CINEMATIC · DRIFTING",
    "Ghost Keys": "HOLLOW · INTIMATE · NOCTURNAL",
    "Custom": "USER-DESIGNED ANALOG PATCH",
}


class SynthVisualizer(WindowClient, QWidget):
    """Live oscillator, filter, signal-flow, and arp activity display."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setFixedHeight(126)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(60)

    @staticmethod
    def _wave(kind: str, phase: float, pulse: float) -> float:
        phase %= 1.0
        if kind == "sine":
            return math.sin(phase * math.tau)
        if kind == "triangle":
            return 1.0 - 4.0 * abs(phase - 0.5)
        if kind == "square":
            return 1.0 if phase < pulse else -1.0
        return 2.0 * phase - 1.0

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        outer = QRectF(self.rect()).adjusted(8, 7, -8, -7)
        p.fillRect(self.rect(), q("bg"))
        p.setBrush(q("canvas"))
        p.setPen(QPen(q("line"), 1))
        p.drawRoundedRect(outer, 5, 5)

        patch = self.app.project.synth
        wave = QRectF(
            outer.left() + 12, outer.top() + 22, outer.width() * 0.49, outer.height() - 52
        )
        filt = QRectF(
            outer.left() + outer.width() * 0.61,
            outer.top() + 22,
            outer.width() * 0.36 - 12,
            outer.height() - 52,
        )

        p.setPen(q("dim2"))
        p.drawText(
            QRectF(wave.left(), outer.top() + 4, wave.width(), 16),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"OSCILLOSCOPE  ·  {PATCH_CHARACTER.get(patch.name, PATCH_CHARACTER['Custom'])}",
        )
        p.drawText(
            QRectF(filt.left(), outer.top() + 4, filt.width(), 16),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"FILTER RESPONSE  ·  {patch.cutoff:.0f} Hz",
        )

        p.setPen(QPen(q("fg", 20), 1))
        for i in range(1, 4):
            y = wave.top() + wave.height() * i / 4
            p.drawLine(QPointF(wave.left(), y), QPointF(wave.right(), y))
            y2 = filt.top() + filt.height() * i / 4
            p.drawLine(QPointF(filt.left(), y2), QPointF(filt.right(), y2))
        for i in range(1, 9):
            x = wave.left() + wave.width() * i / 9
            p.drawLine(QPointF(x, wave.top()), QPointF(x, wave.bottom()))

        points1, points2, mixed = [], [], []
        count = max(80, int(wave.width()))
        for i in range(count):
            frac = i / max(1, count - 1)
            phase = frac * 4.0
            a = self._wave(patch.osc1, phase, patch.pulse_width)
            b = self._wave(
                patch.osc2,
                phase * (2**patch.osc2_octave) * (2 ** (patch.detune / 1200)),
                patch.pulse_width,
            )
            value = a * (1.0 - patch.osc_mix) + b * patch.osc_mix
            value += math.sin(phase * math.pi) * patch.sub * 0.4
            x = wave.left() + frac * wave.width()
            points1.append(QPointF(x, wave.center().y() - a * wave.height() * 0.31))
            points2.append(QPointF(x, wave.center().y() - b * wave.height() * 0.27))
            mixed.append(QPointF(x, wave.center().y() - value * wave.height() * 0.36))
        p.setPen(QPen(q("accent2", 75), 1))
        p.drawPolyline(QPolygonF(points1))
        p.setPen(QPen(q("accent", 80), 1))
        p.drawPolyline(QPolygonF(points2))
        p.setPen(QPen(q("accent_hi"), 2))
        p.drawPolyline(QPolygonF(mixed))

        response = []
        cutoff = max(40.0, patch.cutoff)
        for i in range(max(60, int(filt.width()))):
            frac = i / max(1, int(filt.width()) - 1)
            freq = 30.0 * ((20000.0 / 30.0) ** frac)
            base = 1.0 / math.sqrt(1.0 + (freq / cutoff) ** 4)
            distance = math.log2(max(freq, 1) / cutoff)
            bump = patch.resonance * 0.58 * math.exp(-((distance / 0.33) ** 2))
            magnitude = min(1.18, base + bump)
            response.append(
                QPointF(
                    filt.left() + frac * filt.width(),
                    filt.bottom() - magnitude * filt.height() * 0.78,
                )
            )
        p.setPen(QPen(q("accent_hi"), 2))
        p.drawPolyline(QPolygonF(response))
        p.setPen(q("dim2"))
        p.drawText(QRectF(filt.left(), filt.bottom() - 12, 35, 12), "30")
        p.drawText(QRectF(filt.right() - 42, filt.bottom() - 12, 42, 12), Qt.AlignRight, "20k")

        arp_state = getattr(self.app.engine, "arp_state", None)
        held = sorted(getattr(arp_state, "held", ()))
        arp = self.app.project.arp
        if arp.enabled and arp_state is not None:
            sequence = arp_state.sequence(arp)[:12]
            active = (arp_state.index - 1) % max(1, len(sequence))
            dot_w = 10
            dots_x = filt.right() - len(sequence) * dot_w
            for i, note in enumerate(sequence):
                lit = i == active and bool(held)
                p.setBrush(q("accent_hi") if lit else q("accent", 55))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(dots_x + i * dot_w, filt.top() + 2, 6, 6))
                if lit:
                    p.setPen(q("accent2"))
                    p.drawText(
                        QRectF(dots_x + i * dot_w - 3, filt.top() + 9, 12, 10),
                        Qt.AlignCenter,
                        NOTE_NAMES[note % 12],
                    )
        flow_y = outer.bottom() - 22
        stages = [
            f"{patch.osc1.upper()} + {patch.osc2.upper()}",
            "DRIVE",
            "RESONANT LPF",
            "ADSR / VCA",
            f"TRACK {patch.track + 1}",
        ]
        x = outer.left() + 12
        p.setFont(QFont(self.font().family(), 7))
        for index, stage in enumerate(stages):
            width = max(64, len(stage) * 6 + 13)
            p.setBrush(q("accent", 40 if index < 4 else 75))
            p.setPen(QPen(q("accent", 125), 1))
            p.drawRoundedRect(QRectF(x, flow_y, width, 16), 3, 3)
            p.setPen(q("fg"))
            p.drawText(QRectF(x, flow_y, width, 16), Qt.AlignCenter, stage)
            x += width + 16
            if index < len(stages) - 1:
                p.setPen(q("dim2"))
                p.drawText(QRectF(x - 15, flow_y, 14, 16), Qt.AlignCenter, "→")

        arp_x = filt.left()
        p.setPen(q("accent2") if arp.enabled else q("dim2"))
        held_text = " ".join(NOTE_NAMES[n % 12] + str(n // 12 - 1) for n in held) or "—"
        p.drawText(
            QRectF(arp_x, flow_y, filt.width(), 16),
            Qt.AlignRight | Qt.AlignVCenter,
            f"ARP {arp.mode.upper()}  ·  HELD {held_text}",
        )


class PianoKeyboard(QWidget):
    notePressed = Signal(int, float)
    noteReleased = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_note = 48
        self.octaves = 2
        self.active: set[int] = set()
        self._mouse_note: int | None = None
        self.setMinimumHeight(150)
        self.setMouseTracking(True)

    def notes(self) -> range:
        return range(self.base_note, self.base_note + self.octaves * 12 + 1)

    def _rects(self):
        notes = list(self.notes())
        whites = [n for n in notes if n % 12 not in BLACK_NOTES]
        white_w = self.width() / max(1, len(whites))
        white_rects = {}
        x = 0.0
        for note in whites:
            white_rects[note] = QRectF(x, 0, white_w, self.height())
            x += white_w
        black_rects = {}
        for note in notes:
            if note % 12 not in BLACK_NOTES:
                continue
            previous_whites = sum(1 for n in whites if n < note)
            centre = previous_whites * white_w
            black_rects[note] = QRectF(
                centre - white_w * 0.31, 0, white_w * 0.62, self.height() * 0.62
            )
        return white_rects, black_rects

    def note_at(self, pos) -> int | None:
        whites, blacks = self._rects()
        for note, rect in blacks.items():
            if rect.contains(pos):
                return note
        for note, rect in whites.items():
            if rect.contains(pos):
                return note
        return None

    def set_note_active(self, note: int, active: bool):
        if active:
            self.active.add(note)
        else:
            self.active.discard(note)
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        note = self.note_at(ev.position())
        if note is None:
            return
        self._mouse_note = note
        velocity = 0.65 + 0.35 * min(1.0, ev.position().y() / max(1, self.height()))
        self.set_note_active(note, True)
        self.notePressed.emit(note, velocity)

    def mouseMoveEvent(self, ev):
        if not (ev.buttons() & Qt.LeftButton):
            return
        note = self.note_at(ev.position())
        if note == self._mouse_note:
            return
        if self._mouse_note is not None:
            self.set_note_active(self._mouse_note, False)
            self.noteReleased.emit(self._mouse_note)
        self._mouse_note = note
        if note is not None:
            self.set_note_active(note, True)
            self.notePressed.emit(note, 0.9)

    def mouseReleaseEvent(self, ev):
        if self._mouse_note is not None:
            note, self._mouse_note = self._mouse_note, None
            self.set_note_active(note, False)
            self.noteReleased.emit(note)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), q("bg"))
        whites, blacks = self._rects()
        key_font = QFont(self.font())
        key_font.setPointSizeF(8)
        key_font.setBold(True)
        p.setFont(key_font)
        for note, rect in whites.items():
            active = note in self.active
            p.setBrush(q("accent") if active else q("fg"))
            p.setPen(QPen(q("line"), 1))
            p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            if note % 12 == 0:
                p.setPen(q("on_accent") if active else q("bg"))
                p.drawText(
                    rect.adjusted(4, 0, -3, -5), Qt.AlignBottom | Qt.AlignLeft, f"C{note // 12 - 1}"
                )
            key = MUSICAL_OFFSET_LABELS.get(note - self.base_note)
            if key:
                p.setPen(q("on_accent") if active else q("dim"))
                p.drawText(
                    QRectF(rect.left(), rect.bottom() - 30, rect.width(), 18), Qt.AlignCenter, key
                )
        for note, rect in blacks.items():
            active = note in self.active
            p.setBrush(q("accent_hi") if active else q("canvas"))
            p.setPen(QPen(q("line"), 1))
            p.drawRoundedRect(rect, 2, 2)
            key = MUSICAL_OFFSET_LABELS.get(note - self.base_note)
            if key:
                p.setPen(q("on_accent") if active else q("dim"))
                p.drawText(
                    QRectF(rect.left(), rect.bottom() - 22, rect.width(), 16), Qt.AlignCenter, key
                )


class SynthPanel(WindowClient, QWidget):
    """Hands-on controls for the built-in analog synth and arpeggiator."""

    instrumentReady = Signal(int, str, object, str)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._building = True
        self._load_request = 0
        self._preview_request = -1
        self._preview_generation = 0
        self._preview_note = None
        self.instrumentReady.connect(self._instrument_ready)
        self._controls: list[tuple[QSlider, str, float, float, bool, QLabel, object]] = []
        self._combos: list[tuple[QComboBox, str]] = []
        self.octave = 3
        self._build()
        self._building = False
        self.sync()

    @property
    def base_note(self) -> int:
        return (self.octave + 1) * 12

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("title")
        return label

    @staticmethod
    def _section(text: str):
        frame = QFrame()
        frame.setObjectName("strip")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(7)
        layout.addWidget(SynthPanel._title(text))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        layout.addLayout(form)
        layout.addStretch(1)
        return frame, form

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head.setObjectName("toolbar")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 7, 10, 7)
        hl.addWidget(self._title("SOUNDS"))
        self.category = QComboBox()
        self.category.addItems(
            [
                "All sounds",
                *dict.fromkeys([*orchestra.CATEGORIES.values(), *PATCH_CATEGORIES.values()]),
            ]
        )
        self.category.currentTextChanged.connect(self._filter_presets)

        self.preset = QComboBox()
        self.preset.addItems(list(PATCHES))
        self.preset.setMinimumWidth(210)
        self.preset.setEditable(True)
        self.preset.setInsertPolicy(QComboBox.NoInsert)
        self.preset.completer().setFilterMode(Qt.MatchContains)
        self.preset.setToolTip("Search by name, or choose an instrument category")
        self.preset.setParent(self)
        self.preset.hide()
        preview = QPushButton("Preview sound")
        preview.clicked.connect(self.preview_sound)
        hl.addWidget(preview)
        self.patch_name = QLabel("")
        self.patch_name.setObjectName("clipname")
        hl.insertWidget(1, self.patch_name, 1)
        hl.addStretch(1)
        self.track = QComboBox()
        self.track.addItems([f"{i + 1}" for i in range(len(self.app.project.tracks))])
        self.track.currentIndexChanged.connect(self._track_changed)
        panic = QPushButton("Panic")
        panic.setToolTip("Release all sounding synth notes")
        panic.setObjectName("mini")
        panic.clicked.connect(self.app.engine.synth_panic)
        hl.addWidget(panic)
        self.print_button = QPushButton("PRINT C3 → PAD A1")
        self.print_button.setObjectName("go2")
        self.print_button.setToolTip("Render a two-beat note from this patch onto the selected pad")
        self.print_button.clicked.connect(self.app.print_synth_to_pad)
        hl.addWidget(self.print_button)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        head.setMinimumWidth(head.sizeHint().width())
        scroll.setFixedHeight(50)
        scroll.setWidget(head)
        outer.addWidget(scroll)
        quick, form = self._section("INSTRUMENT")
        self._slider(form, "Brightness", "cutoff", 50, 18000, lambda v: f"{v:.0f} Hz", True)
        self._slider(form, "Soft attack", "attack", 0.0005, 3, lambda v: f"{v:.2f} s", True)
        self._slider(form, "Release", "release", 0.005, 5, lambda v: f"{v:.2f} s", True)
        self._slider(form, "Width", "spread", 0, 1, lambda v: f"{v:.0%}")
        self.instrument_description = QLabel()
        self.instrument_description.setWordWrap(True)
        self.instrument_description.setObjectName("hint")
        form.addRow(self.instrument_description)
        self.sample_controls = QWidget()
        sample_form = QFormLayout(self.sample_controls)
        sample_form.setContentsMargins(0, 0, 0, 0)
        self._slider(sample_form, "Layer blend", "layer_mix", 0, 1, lambda v: f"{v:.0%}")
        self._slider(sample_form, "Movement", "motion", 0, 1, lambda v: f"{v:.0%}")
        self._slider(
            sample_form, "Pulse speed", "lfo_rate", 0.03, 12, lambda v: f"{v:.2f} Hz", True
        )
        self._combo(
            sample_form, "Direction", "sample_reverse", ("Forward", "Reverse"), (False, True)
        )
        form.addRow(self.sample_controls)
        form.addRow("Mixer track", self.track)
        quick.setMinimumWidth(235)
        quick.setMaximumWidth(340)
        self.sound_cards = QListWidget()
        self.sound_cards.setObjectName("soundCards")
        self.sound_cards.setViewMode(QListView.ListMode)
        self.sound_cards.setResizeMode(QListView.Adjust)
        self.sound_cards.setMovement(QListView.Static)
        self.sound_cards.setSpacing(0)
        self.sound_cards.setUniformItemSizes(True)
        self.sound_cards.setWordWrap(False)
        self.sound_cards.setAccessibleName("Factory instruments")
        self.sound_cards.setToolTip("Click to load. Double-click or press Enter to preview.")
        self.sound_cards.itemActivated.connect(self._audition_card)
        self.sound_cards.itemClicked.connect(lambda item: self.load_preset(item.data(Qt.UserRole)))
        self.sound_search = QLineEdit()
        self.sound_search.setPlaceholderText("Search instruments…")
        self.sound_search.setClearButtonEnabled(True)
        self.sound_search.setAccessibleName("Search factory instruments")
        self.sound_search.setToolTip("Type to filter · Down to browse · Enter to load and preview")
        self.sound_search.installEventFilter(self)
        self.sound_search.returnPressed.connect(self._preview_search_result)
        self.sound_search.textChanged.connect(
            lambda text: self._populate_sound_cards(self.category.currentText())
        )
        library = QWidget()
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(10, 8, 10, 8)
        library_layout.setSpacing(6)
        library_controls = QHBoxLayout()
        library_controls.addWidget(self.sound_search, 1)
        library_controls.addWidget(self.category)
        library_layout.addLayout(library_controls)
        library_layout.addWidget(self.sound_cards, 1)
        self.sound_result_count = QLabel()
        self.sound_result_count.setObjectName("hint")
        library_layout.addWidget(self.sound_result_count)
        self._populate_sound_cards("All sounds")
        self.instrument_splitter = QSplitter(Qt.Horizontal)
        self.instrument_splitter.setChildrenCollapsible(False)
        self.instrument_splitter.setHandleWidth(4)
        self.instrument_splitter.addWidget(library)
        self.instrument_splitter.addWidget(quick)
        self.instrument_splitter.setSizes([650, 290])
        outer.addWidget(self.instrument_splitter, 1)
        advanced = QPushButton("Sound design & arpeggiator ▸")
        advanced.setCheckable(True)
        advanced.setMinimumHeight(34)
        outer.addWidget(advanced)

        self.visualizer = SynthVisualizer(self.app)
        outer.addWidget(self.visualizer)

        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(8, 8, 8, 8)
        bl.setSpacing(8)

        osc, of = self._section("OSCILLATORS")
        self.oscillator_section = osc
        self._combo(of, "OSC 1", "osc1", WAVEFORMS)
        self._combo(of, "OSC 2", "osc2", WAVEFORMS)
        self._combo(of, "OCT 2", "osc2_octave", ("-1", "0", "+1"), (-1, 0, 1))
        self._slider(of, "MIX", "osc_mix", 0, 1, lambda v: f"{v * 100:.0f}%")
        self._slider(of, "DETUNE", "detune", 0, 30, lambda v: f"{v:.1f} ct")
        self._slider(of, "PULSE", "pulse_width", 0.1, 0.9, lambda v: f"{v * 100:.0f}%")
        self._slider(of, "SUB", "sub", 0, 0.8, lambda v: f"{v * 100:.0f}%")
        self._slider(of, "NOISE", "noise", 0, 0.35, lambda v: f"{v * 100:.0f}%")
        bl.addWidget(osc, 1)

        filt, ff = self._section("LADDER CHARACTER")
        self.analog_filter_section = filt
        self._slider(
            ff,
            "CUTOFF",
            "cutoff",
            50,
            18000,
            lambda v: f"{v / 1000:.2f}k" if v >= 1000 else f"{v:.0f} Hz",
            True,
        )
        self._slider(ff, "RESONANCE", "resonance", 0, 0.96, lambda v: f"{v * 100:.0f}%")
        self._slider(ff, "ENV", "filter_env", 0, 1, lambda v: f"{v * 100:.0f}%")
        self._slider(ff, "DRIVE", "drive", 0, 1, lambda v: f"{v * 100:.0f}%")
        self._slider(ff, "LFO→FILTER", "lfo_filter", 0, 0.8, lambda v: f"{v * 100:.0f}%")
        bl.addWidget(filt, 1)

        amp, af = self._section("ENVELOPE / MOTION")
        self._slider(af, "ATTACK", "attack", 0.0005, 3, lambda v: f"{v:.3f}s", True)
        self._slider(af, "DECAY", "decay", 0.005, 3, lambda v: f"{v:.3f}s", True)
        self._slider(af, "SUSTAIN", "sustain", 0, 1, lambda v: f"{v * 100:.0f}%")
        self._slider(af, "RELEASE", "release", 0.005, 5, lambda v: f"{v:.3f}s", True)
        self._slider(af, "LFO RATE", "lfo_rate", 0.03, 12, lambda v: f"{v:.2f} Hz", True)
        self._slider(af, "LFO→PITCH", "lfo_pitch", 0, 30, lambda v: f"{v:.1f} ct")
        self._slider(af, "SPREAD", "spread", 0, 1, lambda v: f"{v * 100:.0f}%")
        self._slider(af, "VOLUME", "volume", 0.02, 0.75, lambda v: f"{v * 100:.0f}%")
        bl.addWidget(amp, 1)

        arp, arpf = self._section("ARPEGGIATOR")
        self.arp_on = QPushButton("ARP OFF")
        self.arp_on.setObjectName("go")
        self.arp_on.setCheckable(True)
        self.arp_on.toggled.connect(self._arp_toggled)
        arpf.addRow("", self.arp_on)
        self.arp_rate = QComboBox()
        for label, value in (("1/32", 0.125), ("1/16", 0.25), ("1/8", 0.5), ("1/4", 1.0)):
            self.arp_rate.addItem(label, value)
        self.arp_rate.currentIndexChanged.connect(
            lambda i: self._set_arp("rate_beats", self.arp_rate.itemData(i))
        )
        arpf.addRow("RATE", self.arp_rate)
        self.arp_mode = QComboBox()
        self.arp_mode.addItems(["up", "down", "up/down", "random"])
        self.arp_mode.currentTextChanged.connect(lambda v: self._set_arp("mode", v))
        arpf.addRow("MODE", self.arp_mode)
        self.arp_octaves = QComboBox()
        self.arp_octaves.addItems(["1", "2", "3", "4"])
        self.arp_octaves.currentIndexChanged.connect(lambda i: self._set_arp("octaves", i + 1))
        arpf.addRow("OCTAVES", self.arp_octaves)
        self.arp_gate = QSlider(Qt.Horizontal)
        self.arp_gate.setRange(5, 100)
        self.arp_gate.valueChanged.connect(lambda v: self._set_arp("gate", v / 100))
        arpf.addRow("GATE", self.arp_gate)
        hint = QLabel(
            "Hold a chord. The engine orders notes\nat sample-accurate, BPM-synced intervals."
        )
        hint.setObjectName("hint")
        arp.layout().insertWidget(arp.layout().count() - 1, hint)
        bl.addWidget(arp, 1)
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setWidget(body)
        outer.addWidget(advanced_scroll, 1)
        advanced_scroll.hide()
        self.visualizer.hide()
        advanced.toggled.connect(advanced_scroll.setVisible)
        advanced.toggled.connect(lambda on: self.instrument_splitter.setVisible(not on))
        advanced.toggled.connect(self.visualizer.setVisible)
        advanced.toggled.connect(
            lambda on: advanced.setText(
                "Sound design & arpeggiator ▾" if on else "Sound design & arpeggiator ▸"
            )
        )

        keyboard_bar = QWidget()
        keyboard_bar.setObjectName("toolbar")
        kl = QHBoxLayout(keyboard_bar)
        kl.setContentsMargins(10, 5, 10, 5)
        kl.addWidget(QLabel("TYPING KEYS"))
        kl.addWidget(QLabel("Ctrl+T opens the focused musical-typing window"))
        kl.addStretch(1)
        down = QPushButton("OCT −")
        down.setObjectName("mini")
        down.clicked.connect(lambda: self.set_octave(self.octave - 1))
        up = QPushButton("OCT +")
        up.setObjectName("mini")
        up.clicked.connect(lambda: self.set_octave(self.octave + 1))
        self.octave_label = QLabel("")
        kl.addWidget(down)
        kl.addWidget(self.octave_label)
        kl.addWidget(up)
        outer.addWidget(keyboard_bar)

        self.keyboard = PianoKeyboard()
        self.keyboard.notePressed.connect(self.app.play_synth_note)
        self.keyboard.noteReleased.connect(self.app.release_synth_note)
        outer.addWidget(self.keyboard)
        self.preset.currentTextChanged.connect(self.load_preset)

    def _combo(self, form, label, attr, labels, values=None):
        combo = QComboBox()
        if attr == "sample_reverse":
            combo.setToolTip("Recorded sample direction; applies to the next note")
        values = values or labels
        for text, value in zip(labels, values, strict=True):
            combo.addItem(str(text), value)
        combo.currentIndexChanged.connect(
            lambda i, c=combo, a=attr: self._set_patch(a, c.itemData(i))
        )
        form.addRow(label, combo)
        self._combos.append((combo, attr))

    def _slider(self, form, label, attr, lo, hi, formatter, log=False):
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, 1000)
        slider.setProperty("instrumentDragChanged", False)
        slider.sliderReleased.connect(lambda: slider.setProperty("instrumentDragChanged", False))
        value = QLabel()
        value.setObjectName("hint")
        value.setMinimumWidth(58)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider.valueChanged.connect(
            lambda pos, a=attr, low=lo, high=hi, logarithmic=log, out=value, fmt=formatter: (
                self._slider_changed(pos, a, low, high, logarithmic, out, fmt, slider)
            )
        )
        row.addWidget(slider, 1)
        row.addWidget(value)
        form.addRow(label, host)
        self._controls.append((slider, attr, lo, hi, log, value, formatter))

    @staticmethod
    def _position(value, lo, hi, log):
        if log:
            return (math.log(value) - math.log(lo)) / (math.log(hi) - math.log(lo))
        return (value - lo) / (hi - lo)

    @staticmethod
    def _value(position, lo, hi, log):
        if log:
            return math.exp(math.log(lo) + position * (math.log(hi) - math.log(lo)))
        return lo + position * (hi - lo)

    def _slider_changed(self, pos, attr, lo, hi, log, label, formatter, slider=None):
        value = self._value(pos / 1000, lo, hi, log)
        label.setText(formatter(value))
        snapshot = True
        if not self._building and slider is not None:
            snapshot = not slider.isSliderDown() or not slider.property("instrumentDragChanged")
            slider.setProperty("instrumentDragChanged", slider.isSliderDown())
        self._set_patch(attr, value, snapshot=snapshot)

    def _set_patch(self, attr, value, *, snapshot=True):
        if not self._building:
            if value == getattr(self.app.project.synth, attr):
                return
            self._load_request += 1
            if self.app.project.synth.sample_source and snapshot:
                self.app.snapshot()
            setattr(self.app.project.synth, attr, value)
            self.app.project.synth.name = "Custom"
            self.patch_name.setText("Custom")
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(-1)
            self.preset.blockSignals(False)
            self.visualizer.update()
            self.app._set_dirty(True)
            self.sync()

    def _set_arp(self, attr, value):
        if not self._building and value is not None:
            setattr(self.app.project.arp, attr, value)
            self.visualizer.update()
            self.app._set_dirty(True)

    def _track_changed(self, index):
        if not self._building:
            self.app.project.synth.track = index
            self.app._set_dirty(True)

    def _arp_toggled(self, enabled):
        if self._building:
            return
        self.app.project.arp.enabled = enabled
        self.arp_on.setText("ARP ON" if enabled else "ARP OFF")
        self.app.engine.synth_panic()
        self.visualizer.update()
        self.app._set_dirty(True)

    def _audition_card(self, item):
        if self.load_preset(item.data(Qt.UserRole)):
            self.preview_sound()
        else:
            self._preview_request = self._load_request

    def _search_result(self):
        return self.sound_cards.currentItem() or self.sound_cards.item(0)

    def _preview_search_result(self):
        item = self._search_result()
        if item is not None:
            self.sound_cards.setCurrentItem(item)
            self._audition_card(item)

    def eventFilter(self, watched, event):
        if (
            watched is self.sound_search
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Down
            and event.modifiers() == Qt.NoModifier
        ):
            item = self._search_result()
            if item is not None:
                self.sound_cards.setCurrentItem(item)
                self.sound_cards.setFocus(Qt.TabFocusReason)
            return True
        return super().eventFilter(watched, event)

    def preview_sound(self):
        # Preview goes directly to the engine; it must not record notes.
        note = orchestra.PREVIEW_NOTES.get(self.app.project.synth.name, self.base_note)
        self._preview_generation += 1
        generation = self._preview_generation
        if self._preview_note is not None:
            self.app.engine.synth_note_off(self._preview_note)
        self._preview_note = note
        self.app.engine.synth_note_on(note, 0.7)
        duration = 1800 if self.app.project.synth.sample_source else 650

        def release_preview():
            if generation == self._preview_generation:
                self.app.engine.synth_note_off(note)
                self._preview_note = None

        QTimer.singleShot(duration, self, release_preview)

    def _populate_sound_cards(self, category):
        self.sound_cards.clear()
        query = self.sound_search.text().strip().casefold() if hasattr(self, "sound_search") else ""
        for name in [
            *orchestra.PATCHES,
            *(name for name in PATCHES if name not in orchestra.PATCHES),
        ]:
            if category != "All sounds" and PATCH_CATEGORIES[name] != category:
                continue
            description = PATCH_DESCRIPTIONS.get(name, "")
            haystack = f"{name} {PATCH_CATEGORIES[name]} {description}".casefold()
            if not all(word in haystack for word in query.split()):
                continue
            item = QListWidgetItem(f"{name}   ·   {PATCH_CATEGORIES[name]}")
            item.setData(Qt.UserRole, name)
            item.setSizeHint(QSize(180, 32))
            item.setToolTip(
                f"{name} · {PATCH_CATEGORIES[name]}\n{description}\nEnter to preview; click to load"
            )
            self.sound_cards.addItem(item)
            item.setSelected(name == self.app.project.synth.name)
        count = self.sound_cards.count()
        self.sound_result_count.setText(
            f"{count} instruments · click to load · Enter to preview"
            if count
            else "No matches · clear the search or choose All sounds"
        )

    def _filter_presets(self, category):
        if not hasattr(self, "preset"):
            return
        self._populate_sound_cards(category)
        selected = self.app.project.synth.name
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItems(
            [
                name
                for name in PATCHES
                if category == "All sounds" or PATCH_CATEGORIES[name] == category
            ]
        )
        self.preset.setCurrentIndex(self.preset.findText(selected))
        self.preset.blockSignals(False)

    def load_preset(self, name):
        if self._building or name not in PATCHES:
            return
        self._load_request += 1
        if name == self.app.project.synth.name:
            return True
        patch = PATCHES[name]
        if patch.sample_source and not orchestra.is_prepared(patch):
            request, project = self._load_request, self.app.project
            self.app.status.showMessage(f"Loading orchestral recordings · {name}…")

            def prepare():
                error = ""
                try:
                    orchestra.prepare_patch(patch)
                except Exception as exc:
                    error = str(exc)
                emit_if_alive(self, "instrumentReady", request, name, project, error)

            threading.Thread(target=prepare, name="anharmonic-instrument-load", daemon=True).start()
            return False
        self._commit_preset(name)
        return True

    def _instrument_ready(self, request, name, project, error):
        if request != self._load_request or project is not self.app.project:
            return
        if error:
            self.app.status.showMessage(f"Instrument unavailable · {error[:160]}", 7000)
            return
        self._commit_preset(name)
        if self._preview_request == request:
            self.preview_sound()

    def _commit_preset(self, name):
        self.app.snapshot()
        self.app.engine.synth_panic()
        track = self.app.project.synth.track
        self.app.project.synth = patch_copy(name)
        self.app.project.synth.track = track
        self.sync()
        kind = "orchestral instrument" if self.app.project.synth.sample_source else "analog patch"
        self.app.status.showMessage(f"{kind} · {name}", 3500)

    def set_octave(self, octave):
        self.octave = max(1, min(6, int(octave)))
        self.keyboard.base_note = self.base_note
        self.keyboard.update()
        self.octave_label.setText(f"C{self.octave}")
        self.sync_target_pad()
        typing = getattr(self.app, "typing_keyboard", None)
        if typing is not None:
            typing.sync()

    def sync_target_pad(self):
        if not hasattr(self.app, "pads"):
            return
        gi = self.app.pads.selected
        bank = chr(ord("A") + gi // 16)
        self.print_button.setText(f"PRINT C{self.octave} → PAD {bank}{gi % 16 + 1}")

    def sync(self):
        self._building = True
        patch = self.app.project.synth
        self.patch_name.setText(patch.name)
        sampled = bool(patch.sample_source)
        self.sample_controls.setVisible(sampled)
        self.oscillator_section.setVisible(not sampled)
        self.analog_filter_section.setVisible(not sampled)
        self.visualizer.setVisible(not sampled)
        self.instrument_description.setText(
            PATCH_DESCRIPTIONS.get(patch.name, "")
            or (
                orchestra.SOURCE_NAMES.get(patch.sample_source, "")
                if sampled
                else "Analog synthesis"
            )
        )
        self.sound_cards.setStyleSheet(
            f"QListWidget {{ background: {q('bg').name()}; border: none; }}"
            f"QListWidget::item {{ color: {q('fg').name()}; border: none; border-bottom: 1px solid {q('line').name()}; padding: 5px 8px; }}"
            f"QListWidget::item:selected {{ background: {q('item_sel').name()}; border-left: 2px solid {q('accent').name()}; }}"
            f"QListWidget::item:hover {{ background: {q('item_hover').name()}; }}"
            f"QListWidget:focus {{ border: 1px solid {q('accent').name()}; }}"
        )
        for index in range(self.sound_cards.count()):
            item = self.sound_cards.item(index)
            item.setSelected(item.data(Qt.UserRole) == patch.name)
        preset_index = self.preset.findText(patch.name)
        if preset_index >= 0:
            self.preset.setCurrentIndex(preset_index)
        else:
            self.preset.setEditText(patch.name)
        for slider, attr, lo, hi, log, label, formatter in self._controls:
            slider.setEnabled(
                not (
                    sampled
                    and (attr == "lfo_pitch" or (attr == "layer_mix" and not patch.sample_layer))
                )
            )
            value = min(hi, max(lo, float(getattr(patch, attr))))
            slider.setValue(round(self._position(value, lo, hi, log) * 1000))
            label.setText(formatter(value))
        for combo, attr in self._combos:
            index = combo.findData(getattr(patch, attr))
            combo.setCurrentIndex(max(0, index))
        self.track.clear()
        self.track.addItems(
            [f"{i + 1} · {track.name}" for i, track in enumerate(self.app.project.tracks)]
        )
        self.track.setCurrentIndex(max(0, min(len(self.app.project.tracks) - 1, patch.track)))
        arp = self.app.project.arp
        self.arp_on.setChecked(arp.enabled)
        self.arp_on.setText("ARP ON" if arp.enabled else "ARP OFF")
        self.arp_rate.setCurrentIndex(max(0, self.arp_rate.findData(arp.rate_beats)))
        self.arp_mode.setCurrentText(arp.mode)
        self.arp_octaves.setCurrentIndex(max(0, min(3, arp.octaves - 1)))
        self.arp_gate.setValue(round(arp.gate * 100))
        self.set_octave(self.octave)
        self.visualizer.update()
        self._building = False
