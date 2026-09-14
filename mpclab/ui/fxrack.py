"""The mixer's effect rack: one insert chain, the two sends, and the master bus.

Deliberately one column of plain labelled sliders rather than a wall of knobs.
Every control says what it is and what it is currently worth, the EQ curve
redraws as the tone moves, and the compressor's gain reduction is visible while
it works — so a chain can be dialled in by eye without hunting through menus.
"""

from __future__ import annotations

import numpy as np
from .window_client import WindowClient

from PySide6.QtCore import Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen, QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QPushButton,
    QComboBox,
    QScrollArea,
    QFrame,
    QSizePolicy,
)

from ..fx import TrackChain, cascade_response
from .theme import q, TRACK_COLORS

# Ready-made chains, so a track can be shaped in one click and then adjusted.
# Every key here is a TrackFX field; anything not named is reset to default.
TRACK_PRESETS: dict[str, dict] = {
    "clean": {},
    "punchy drums": dict(
        low=2.0,
        high=1.5,
        comp=True,
        threshold=-16.0,
        ratio=4.0,
        attack=0.005,
        release=0.09,
        makeup=3.0,
    ),
    "fat bass": dict(
        low=3.5,
        mid=-1.5,
        mid_freq=400.0,
        high=-3.0,
        drive=0.22,
        comp=True,
        threshold=-18.0,
        ratio=3.0,
        attack=0.015,
        release=0.14,
        makeup=2.0,
    ),
    "tape warmth": dict(low=1.0, high=-2.5, drive=0.35),
    "lo-fi": dict(filter_type="lowpass", cutoff=3800.0, resonance=0.1, high=-6.0, drive=0.3),
    "telephone": dict(
        filter_type="highpass",
        cutoff=650.0,
        resonance=0.2,
        mid=6.0,
        mid_freq=1600.0,
        high=-10.0,
        low=-8.0,
    ),
    "air": dict(high=4.0, mid=-1.0, mid_freq=450.0),
    "sub only": dict(filter_type="lowpass", cutoff=170.0, resonance=0.2),
    "vocal ride": dict(
        low=-2.0,
        high=2.5,
        comp=True,
        threshold=-22.0,
        ratio=4.0,
        attack=0.008,
        release=0.15,
        makeup=3.5,
    ),
    "wide space": dict(high=1.5, send_reverb=0.42, send_delay=0.18),
}

FILTER_TYPES = ("off", "lowpass", "highpass")
SYNC_LABELS = ("1/4", "1/8.", "1/8", "1/8T", "1/16", "1/16T")


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("title")
    return label


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    return label


class ParamSlider(QWidget):
    """Label · slider · live value, on one compact line."""

    changed = Signal(float)

    def __init__(
        self,
        label: str,
        lo: float,
        hi: float,
        value: float,
        fmt,
        steps: int = 1000,
        tip: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.lo, self.hi, self.steps, self.fmt = lo, hi, steps, fmt
        self._quiet = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.name = _hint(label)
        self.name.setFixedWidth(58)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, steps)
        self.value = _hint("")
        self.value.setFixedWidth(52)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.name)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value)
        if tip:
            self.setToolTip(tip)
            self.slider.setToolTip(tip)

        self.slider.valueChanged.connect(self._moved)
        self.set_value(value)

    def _to_slider(self, value: float) -> int:
        span = self.hi - self.lo
        return int(round((float(value) - self.lo) / span * self.steps))

    def _from_slider(self, ticks: int) -> float:
        return self.lo + (ticks / self.steps) * (self.hi - self.lo)

    def set_value(self, value: float) -> None:
        self._quiet = True
        self.slider.setValue(max(0, min(self.steps, self._to_slider(value))))
        self.value.setText(self.fmt(float(value)))
        self._quiet = False

    def _moved(self, ticks: int) -> None:
        value = self._from_slider(ticks)
        self.value.setText(self.fmt(value))
        if not self._quiet:
            self.changed.emit(value)

    def bind(self, target, field: str, after=None):
        """Write straight onto a settings dataclass when the slider moves."""

        def apply(value: float):
            setattr(target, field, value)
            if after:
                after()

        self.changed.connect(apply)
        return self


class Section(QFrame):
    """A titled block, optionally with an enable toggle in its header."""

    def __init__(self, title: str, toggle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("fxSection")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(9, 7, 9, 9)
        outer.setSpacing(5)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addWidget(_title(title))
        head.addStretch(1)
        self.toggle = None
        if toggle:
            self.toggle = QPushButton(toggle)
            self.toggle.setObjectName("mini")
            self.toggle.setCheckable(True)
            head.addWidget(self.toggle)
        outer.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(4)
        outer.addLayout(self.body)

    def add(self, widget) -> None:
        self.body.addWidget(widget)

    def add_row(self, *widgets, stretch_last: bool = True) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for i, widget in enumerate(widgets):
            if isinstance(widget, str):
                widget = _hint(widget)
            lay.addWidget(widget, 1 if (stretch_last and i == len(widgets) - 1) else 0)
        self.body.addWidget(row)
        return row


class EQCurve(QWidget):
    """The track's tone response, 20 Hz – 20 kHz, redrawn as the sliders move."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(74)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.sections: list = []
        self.freqs = np.geomspace(20.0, 20_000.0, 220)
        self.setToolTip("Combined response of this track's tone controls")

    def set_sections(self, sections) -> None:
        self.sections = list(sections)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.fillRect(self.rect(), q("canvas"))

        span_db = 18.0

        def y_for(db: float) -> float:
            return rect.center().y() - (db / span_db) * (rect.height() / 2)

        def x_for(hz: float) -> float:
            lo, hi = np.log10(20.0), np.log10(20_000.0)
            return rect.left() + (np.log10(hz) - lo) / (hi - lo) * rect.width()

        p.setPen(QPen(q("line"), 1))
        for hz in (100, 1_000, 10_000):
            x = x_for(hz)
            p.drawLine(
                QRectF(x, rect.top(), 0, rect.height()).topLeft(),
                QRectF(x, rect.bottom(), 0, 0).topLeft(),
            )
        for db in (-12, 12):
            y = y_for(db)
            p.drawLine(
                QRectF(rect.left(), y, 0, 0).topLeft(), QRectF(rect.right(), y, 0, 0).topLeft()
            )
        p.setPen(QPen(q("dim2"), 1, Qt.DashLine))
        p.drawLine(
            QRectF(rect.left(), y_for(0), 0, 0).topLeft(),
            QRectF(rect.right(), y_for(0), 0, 0).topLeft(),
        )

        response = cascade_response(self.sections, self.freqs)
        db = 20.0 * np.log10(np.abs(response) + 1e-9)
        path = QPainterPath()
        for i, (hz, value) in enumerate(zip(self.freqs, db, strict=True)):
            point = (x_for(hz), max(rect.top(), min(rect.bottom(), y_for(value))))
            path.moveTo(*point) if i == 0 else path.lineTo(*point)
        p.setPen(QPen(q("accent"), 2))
        p.drawPath(path)

        tiny = QFont(self.font())
        tiny.setPointSizeF(6.5)
        p.setFont(tiny)
        p.setPen(q("dim2"))
        for hz, text in ((100, "100"), (1_000, "1k"), (10_000, "10k")):
            p.drawText(QRectF(x_for(hz) + 2, rect.bottom() - 11, 30, 10), Qt.AlignLeft, text)


class GainReduction(QWidget):
    """Downward bar showing how hard the compressor is pulling, in dB."""

    def __init__(self, span_db: float = 18.0, parent=None):
        super().__init__(parent)
        self.span = span_db
        self.value = 0.0
        self.setFixedHeight(8)
        self.setToolTip("Gain reduction")

    def set_value(self, db: float) -> None:
        smoothed = max(float(db), self.value * 0.72)
        if abs(smoothed - self.value) > 0.05:
            self.value = smoothed
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), q("canvas"))
        fraction = min(1.0, self.value / self.span)
        if fraction > 0.001:
            width = self.width() * fraction
            p.fillRect(QRectF(self.width() - width, 0, width, self.height()), q("meter_mid"))


class FXRack(WindowClient, QScrollArea):
    """Insert chain for the selected track, plus the shared sends and master."""

    changed = Signal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.index = 0
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(300)
        self.setMaximumWidth(400)
        self._body = QWidget()
        self.setWidget(self._body)
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(10, 10, 10, 10)
        self._layout.setSpacing(9)
        self._building = False
        self._gr: GainReduction | None = None
        self._eq: EQCurve | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60)
        self.rebuild()

    # ── plumbing ─────────────────────────────────────────────
    def set_track(self, index: int) -> None:
        if index != self.index:
            self.index = index
            self.rebuild()

    def _fx(self):
        return self.app.project.tracks[self.index].fx

    def _touched(self) -> None:
        if self._building:
            return
        if self._eq is not None:
            self._eq.set_sections(TrackChain.tone_sections(self._fx()))
        # Tone IR and its convolution spectrum are built here, on Qt's thread,
        # then transferred through the engine command queue. Request-key
        # caching makes this effectively free for compressor/send-only moves.
        self.app.engine.prepare_fx(self.app.project)
        self.changed.emit()

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _tick(self) -> None:
        if not self.isVisible() or self._gr is None:
            return
        chain = self.app.engine.rack.tracks[self.index]
        self._gr.set_value(chain.comp.gain_reduction_db if self._fx().comp else 0.0)

    # ── build ────────────────────────────────────────────────
    def rebuild(self) -> None:
        self._building = True
        self._clear()
        self._gr = None
        self._eq = None
        proj = self.app.project
        track = proj.tracks[self.index]
        fx = track.fx

        head = QLabel(f"{self.index + 1} · {track.name}")
        head.setObjectName("title")
        head.setStyleSheet(
            f"color: {TRACK_COLORS[self.index % len(TRACK_COLORS)]};"
            "font-size: 12px; letter-spacing: 2px;"
        )
        self._layout.addWidget(head)

        preset = QComboBox()
        preset.addItem("— preset —")
        preset.addItems(TRACK_PRESETS.keys())
        preset.setToolTip("Load a ready-made chain onto this track, then adjust")
        preset.activated.connect(self._apply_preset)
        self._layout.addWidget(preset)

        self._layout.addWidget(self._tone_section(fx))
        self._layout.addWidget(self._dynamics_section(fx))
        self._layout.addWidget(self._sends_section(fx))
        self._layout.addWidget(self._delay_section(proj.delay_fx))
        self._layout.addWidget(self._reverb_section(proj.reverb_fx))
        self._layout.addWidget(self._master_section(proj.master_fx))
        self._layout.addStretch(1)
        self._building = False

    def _tone_section(self, fx) -> Section:
        section = Section("TONE")
        self._eq = EQCurve()
        self._eq.set_sections(TrackChain.tone_sections(fx))
        section.add(self._eq)

        def db(v):
            return f"{v:+.1f}dB"

        section.add(
            ParamSlider("HIGH", -18, 18, fx.high, db, 360, "Shelf above 6 kHz").bind(
                fx, "high", self._touched
            )
        )
        section.add(
            ParamSlider("MID", -18, 18, fx.mid, db, 360, "Bell at the frequency below").bind(
                fx, "mid", self._touched
            )
        )
        section.add(
            ParamSlider(
                "MID Hz",
                150,
                8000,
                fx.mid_freq,
                lambda v: f"{v:.0f}",
                400,
                "Where the mid bell sits",
            ).bind(fx, "mid_freq", self._touched)
        )
        section.add(
            ParamSlider("LOW", -18, 18, fx.low, db, 360, "Shelf below 120 Hz").bind(
                fx, "low", self._touched
            )
        )

        filt = QComboBox()
        filt.addItems(FILTER_TYPES)
        filt.setCurrentText(fx.filter_type)
        filt.setToolTip("Sweepable filter after the EQ")
        filt.currentTextChanged.connect(
            lambda text: (setattr(fx, "filter_type", text), self._touched())
        )
        section.add_row("FILTER", filt)

        section.add(
            ParamSlider(
                "CUTOFF",
                30,
                20000,
                fx.cutoff,
                lambda v: f"{v / 1000:.2f}k" if v >= 1000 else f"{v:.0f}",
                600,
                "Filter corner frequency",
            ).bind(fx, "cutoff", self._touched)
        )
        section.add(
            ParamSlider(
                "RES",
                0.0,
                1.0,
                fx.resonance,
                lambda v: f"{v * 100:.0f}%",
                100,
                "Emphasis at the corner",
            ).bind(fx, "resonance", self._touched)
        )
        return section

    def _dynamics_section(self, fx) -> Section:
        section = Section("DRIVE / COMP", "COMP")
        section.add(
            ParamSlider(
                "DRIVE",
                0.0,
                1.0,
                fx.drive,
                lambda v: f"{v * 100:.0f}%",
                100,
                "Analog-style saturation",
            ).bind(fx, "drive", self._touched)
        )

        section.toggle.setChecked(fx.comp)
        section.toggle.setToolTip("Compress this track")
        section.toggle.toggled.connect(lambda on: (setattr(fx, "comp", on), self._touched()))

        self._gr = GainReduction()
        section.add(self._gr)
        section.add(
            ParamSlider(
                "THRESH",
                -48,
                0,
                fx.threshold,
                lambda v: f"{v:.0f}dB",
                480,
                "Level where compression starts",
            ).bind(fx, "threshold", self._touched)
        )
        section.add(
            ParamSlider(
                "RATIO",
                1.0,
                20.0,
                fx.ratio,
                lambda v: f"{v:.1f}:1",
                190,
                "How hard it holds above the threshold",
            ).bind(fx, "ratio", self._touched)
        )
        section.add(
            ParamSlider(
                "ATTACK",
                0.001,
                0.12,
                fx.attack,
                lambda v: f"{v * 1000:.0f}ms",
                200,
                "Fast keeps transients down, slow lets them through",
            ).bind(fx, "attack", self._touched)
        )
        section.add(
            ParamSlider(
                "RELEASE",
                0.02,
                1.0,
                fx.release,
                lambda v: f"{v * 1000:.0f}ms",
                200,
                "How quickly the level comes back",
            ).bind(fx, "release", self._touched)
        )
        section.add(
            ParamSlider(
                "MAKEUP",
                0.0,
                18.0,
                fx.makeup,
                lambda v: f"+{v:.1f}dB",
                180,
                "Level put back after compression",
            ).bind(fx, "makeup", self._touched)
        )
        return section

    def _sends_section(self, fx) -> Section:
        section = Section("SENDS")

        def pct(v):
            return f"{v * 100:.0f}%"

        section.add(
            ParamSlider(
                "DELAY", 0.0, 1.0, fx.send_delay, pct, 100, "How much of this track feeds the delay"
            ).bind(fx, "send_delay", self._touched)
        )
        section.add(
            ParamSlider(
                "REVERB",
                0.0,
                1.0,
                fx.send_reverb,
                pct,
                100,
                "How much of this track feeds the reverb",
            ).bind(fx, "send_reverb", self._touched)
        )
        return section

    def _delay_section(self, delay) -> Section:
        section = Section("DELAY  (shared)", "ON")
        section.toggle.setChecked(delay.enabled)
        section.toggle.toggled.connect(lambda on: (setattr(delay, "enabled", on), self._touched()))

        sync = QComboBox()
        sync.addItems(SYNC_LABELS)
        sync.setCurrentText(delay.sync)
        sync.setToolTip("Delay time, locked to project tempo")
        sync.currentTextChanged.connect(
            lambda text: (setattr(delay, "sync", text), self._touched())
        )
        section.add_row("TIME", sync)

        def pct(v):
            return f"{v * 100:.0f}%"

        section.add(
            ParamSlider("FEEDBACK", 0.0, 0.95, delay.feedback, pct, 95, "How many repeats").bind(
                delay, "feedback", self._touched
            )
        )
        section.add(
            ParamSlider(
                "DAMPING", 0.0, 1.0, delay.damping, pct, 100, "Repeats get darker as they fade"
            ).bind(delay, "damping", self._touched)
        )
        section.add(
            ParamSlider("LEVEL", 0.0, 1.5, delay.level, pct, 150, "Return level into the mix").bind(
                delay, "level", self._touched
            )
        )

        ping = QPushButton("PING-PONG")
        ping.setObjectName("mini")
        ping.setCheckable(True)
        ping.setChecked(delay.ping_pong)
        ping.setToolTip("Repeats alternate between left and right")
        ping.toggled.connect(lambda on: (setattr(delay, "ping_pong", on), self._touched()))
        section.add(ping)
        return section

    def _reverb_section(self, reverb) -> Section:
        section = Section("REVERB  (shared)", "ON")
        section.toggle.setChecked(reverb.enabled)
        section.toggle.toggled.connect(lambda on: (setattr(reverb, "enabled", on), self._touched()))

        def pct(v):
            return f"{v * 100:.0f}%"

        section.add(
            ParamSlider("SIZE", 0.0, 1.0, reverb.size, pct, 100, "How long the tail runs").bind(
                reverb, "size", self._touched
            )
        )
        section.add(
            ParamSlider(
                "DAMPING", 0.0, 1.0, reverb.damping, pct, 100, "How fast the top end disappears"
            ).bind(reverb, "damping", self._touched)
        )
        section.add(
            ParamSlider(
                "WIDTH", 0.0, 1.0, reverb.width, pct, 100, "Stereo spread of the tail"
            ).bind(reverb, "width", self._touched)
        )
        section.add(
            ParamSlider(
                "PREDELAY",
                0.0,
                0.15,
                reverb.predelay,
                lambda v: f"{v * 1000:.0f}ms",
                150,
                "Gap before the tail starts, keeps hits clear",
            ).bind(reverb, "predelay", self._touched)
        )
        section.add(
            ParamSlider(
                "LEVEL", 0.0, 1.5, reverb.level, pct, 150, "Return level into the mix"
            ).bind(reverb, "level", self._touched)
        )
        return section

    def _master_section(self, master) -> Section:
        section = Section("MASTER BUS", "GLUE")
        section.toggle.setChecked(master.glue)
        section.toggle.setToolTip("Slow bus compression across the whole mix")
        section.toggle.toggled.connect(lambda on: (setattr(master, "glue", on), self._touched()))

        def db(v):
            return f"{v:+.1f}dB"

        section.add(
            ParamSlider("HIGH", -12, 12, master.high, db, 240).bind(master, "high", self._touched)
        )
        section.add(
            ParamSlider("MID", -12, 12, master.mid, db, 240).bind(master, "mid", self._touched)
        )
        section.add(
            ParamSlider("LOW", -12, 12, master.low, db, 240).bind(master, "low", self._touched)
        )
        section.add(
            ParamSlider(
                "DRIVE",
                0.0,
                1.0,
                master.drive,
                lambda v: f"{v * 100:.0f}%",
                100,
                "Saturation across the mix",
            ).bind(master, "drive", self._touched)
        )
        section.add(
            ParamSlider(
                "GLUE",
                0.0,
                1.0,
                master.glue_amount,
                lambda v: f"{v * 100:.0f}%",
                100,
                "How hard the bus compressor works",
            ).bind(master, "glue_amount", self._touched)
        )
        section.add(_hint("A -1 dBFS limiter always follows this section."))
        return section

    # ── presets ──────────────────────────────────────────────
    def _apply_preset(self, row: int) -> None:
        if row <= 0:
            return
        name = list(TRACK_PRESETS)[row - 1]
        fx = self._fx()
        # Start from a clean chain so presets never stack onto each other, but
        # leave the sends alone unless the preset actually asks for them.
        keep = (fx.send_delay, fx.send_reverb)
        for field, default in type(fx)().__dict__.items():
            setattr(fx, field, default)
        fx.send_delay, fx.send_reverb = keep
        for field, value in TRACK_PRESETS[name].items():
            setattr(fx, field, value)
        self.rebuild()
        self._touched()
        self.app.status.showMessage(f"track {self.index + 1}: {name}", 2500)
