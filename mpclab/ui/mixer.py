"""Mixer strips with live VU meters, beside the effect rack for the selected one."""

from __future__ import annotations

import numpy as np
from .window_client import WindowClient

from PySide6.QtCore import Qt, QRectF, Signal, QTimer
from PySide6.QtGui import QPainter, QLinearGradient
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QSlider,
    QPushButton,
    QInputDialog,
    QScrollArea,
    QFrame,
    QSizePolicy,
)

from ..model import NTRACKS
from ..music import automation_values
from .fxrack import FXRack
from .theme import q, TRACK_COLORS


class VUMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(9)
        self.level = 0.0
        self.peak = 0.0

    def set_level(self, rms: float, peak: float | None = None):
        self.level = max(0.0, float(rms))
        instant_peak = self.level if peak is None else max(0.0, float(peak))
        self.peak = max(self.peak * 0.94, instant_peak)
        self.update()

    @staticmethod
    def _position(linear: float) -> float:
        """Map a linear amplitude onto a conventional -60..0 dBFS meter."""
        db = 20.0 * np.log10(max(1e-6, float(linear)))
        return float(np.clip((db + 60.0) / 60.0, 0.0, 1.0))

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), q("canvas"))
        h = self.height()
        v = self._position(self.level)
        grad = QLinearGradient(0, h, 0, 0)
        grad.setColorAt(0.0, q("ok"))
        grad.setColorAt(0.75, q("meter_mid"))
        grad.setColorAt(1.0, q("rec"))
        p.fillRect(QRectF(0, h * (1 - v), self.width(), h * v), grad)
        pk = self._position(self.peak)
        if pk > 0.01:
            p.fillRect(QRectF(0, h * (1 - pk) - 1, self.width(), 2), q("fg", 170))


class _TrackLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class Strip(WindowClient, QWidget):
    changed = Signal()
    picked = Signal(int)

    def __init__(self, app, index: int, master: bool = False, parent=None):
        super().__init__(parent)
        self.app = app
        self.index = index
        self.master = master
        self.setObjectName("masterStrip" if master else "strip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(94 if master else 78, 352)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 7, 6, 7)
        lay.setSpacing(5)

        self.name = _TrackLabel("MASTER" if master else "")
        self.name.setAlignment(Qt.AlignCenter)
        self.name.setObjectName("hint")
        self.name.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.name)

        self.sub = QLabel("")
        self.sub.setObjectName("hint")
        self.sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.sub)

        self.pan = QSlider(Qt.Horizontal)
        self.pan.setRange(-100, 100)
        self.pan.valueChanged.connect(self._pan_changed)
        if master:
            self.pan.setVisible(False)
        lay.addWidget(self.pan)

        mid = QHBoxLayout()
        mid.setSpacing(7)
        self.fader = QSlider(Qt.Vertical)
        self.fader.setRange(0, 130)
        self.fader.setFixedHeight(180)
        self.fader.valueChanged.connect(self._gain_changed)
        self.meter = VUMeter()
        self.meter.setFixedHeight(180)
        mid.addStretch(1)
        mid.addWidget(self.fader)
        mid.addWidget(self.meter)
        mid.addStretch(1)
        lay.addLayout(mid)

        self.value = QLabel("0.0")
        self.value.setObjectName("hint")
        self.value.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.value)

        if not master:
            self.fx_badge = QPushButton("FX")
            self.fx_badge.setObjectName("mini")
            self.fx_badge.setCheckable(True)
            self.fx_badge.setToolTip("This track's effect chain — click to edit it in the rack")
            self.fx_badge.clicked.connect(lambda: self.picked.emit(self.index))
            lay.addWidget(self.fx_badge)

            btns = QHBoxLayout()
            btns.setSpacing(4)
            self.mute = QPushButton("M")
            self.mute.setObjectName("mini")
            self.mute.setCheckable(True)
            self.mute.toggled.connect(self._mute_changed)
            self.solo = QPushButton("S")
            self.solo.setObjectName("accent2")
            self.solo.setCheckable(True)
            self.solo.toggled.connect(self._solo_changed)
            btns.addWidget(self.mute)
            btns.addWidget(self.solo)
            lay.addLayout(btns)
            self.name.clicked.connect(self._rename)
        lay.addStretch(1)
        self.setCursor(Qt.PointingHandCursor)
        self._syncing = False
        self.sync()

    def mousePressEvent(self, ev):
        # Clicking anywhere on a strip brings it up in the rack, so the two
        # halves of the mixer always agree on which track is being worked on.
        if not self.master:
            self.picked.emit(self.index)
        super().mousePressEvent(ev)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _track(self):
        return self.app.project.tracks[self.index]

    def _rename(self):
        name, ok = QInputDialog.getText(
            self, "Rename track", "Track name:", text=self._track().name
        )
        if ok and name:
            self._track().name = name
            self.sync()
            self.changed.emit()

    def _gain_changed(self, v):
        if self._syncing:
            return
        if self.master:
            self.app.project.master = v / 100.0
            transport = getattr(self.app, "master_slider", None)
            if transport is not None and transport.value() != v:
                transport.blockSignals(True)
                transport.setValue(v)
                transport.blockSignals(False)
        else:
            self._track().gain = v / 100.0
        self._update_value()
        self.changed.emit()

    def _pan_changed(self, v):
        if self._syncing or self.master:
            return
        self._track().pan = v / 100.0
        self.changed.emit()

    def _mute_changed(self, b):
        if not self._syncing:
            self._track().mute = b
            self.changed.emit()

    def _solo_changed(self, b):
        if not self._syncing:
            self._track().solo = b
            self.changed.emit()

    def _update_value(self):
        g = self.app.project.master if self.master else self._track().gain
        self.value.setText("-inf" if g <= 0.005 else f"{20 * np.log10(g):+.1f}")

    def sync(self):
        self._syncing = True
        if self.master:
            self.fader.setValue(int(self.app.project.master * 100))
        else:
            t = self._track()
            self.name.setText(t.name)
            self.name.setStyleSheet(f"color: {TRACK_COLORS[self.index % len(TRACK_COLORS)]}")
            self.fader.setValue(int(t.gain * 100))
            self.pan.setValue(int(t.pan * 100))
            self.mute.setChecked(t.mute)
            self.solo.setChecked(t.solo)
            n = sum(1 for p in self.app.project.pads if p.sample_id and p.track == self.index)
            self.sub.setText(f"{n} pad{'' if n == 1 else 's'}")
            parts = self._fx_parts(t.fx)
            self.fx_badge.setChecked(bool(parts))
            # A narrow strip cannot hold four labels, so it shows the first two
            # and a plus; the tooltip always spells the whole chain out.
            self.fx_badge.setText(
                "·".join(parts[:2]) + ("+" if len(parts) > 2 else "") if parts else "FX"
            )
            self.fx_badge.setToolTip(
                "Effects on this track: "
                + ", ".join(parts)
                + "\nClick to edit the chain in the rack"
                if parts
                else "No effects on this track yet — click to open its rack"
            )
        self._update_value()
        self._syncing = False

    @staticmethod
    def _fx_parts(fx) -> list[str]:
        """What is switched on, so a chain is readable from the strip."""
        parts = []
        if fx.tone_active:
            parts.append("EQ" if fx.filter_type == "off" else "FLT")
        if fx.drive > 1e-4:
            parts.append("DRV")
        if fx.comp:
            parts.append("CMP")
        if fx.sends_active:
            parts.append("SND")
        return parts

    def update_meter(self):
        eng = self.app.engine
        project = self.app.project
        target = "master" if self.master else f"track:{self.index}:gain"
        pan_target = f"track:{self.index}:pan"
        active = (
            {a.target for a in project.automation if a.enabled and a.points}
            if eng.mode == "song"
            else set()
        )
        gain_auto = target in active
        pan_auto = not self.master and pan_target in active
        was_auto = getattr(self, "_automation_displayed", False)
        self._automation_displayed = gain_auto or pan_auto
        if self._automation_displayed or was_auto:
            gain = project.master if self.master else self._track().gain
            if gain_auto:
                gain = float(automation_values(project, target, eng.beat, gain))
            self.fader.blockSignals(True)
            self.fader.setValue(round(gain * 100))
            self.fader.blockSignals(False)
            self.fader.setEnabled(not gain_auto)
            self.fader.setToolTip(
                "Automation read: bypass this lane in Automation to adjust manually"
                if gain_auto
                else "Track level"
            )
            self.value.setText(
                ("A " if gain_auto else "")
                + ("-inf" if gain <= 0.005 else f"{20 * np.log10(gain):+.1f}")
            )
            if not self.master:
                pan = self._track().pan
                if pan_auto:
                    pan = float(automation_values(project, pan_target, eng.beat, pan))
                self.pan.blockSignals(True)
                self.pan.setValue(round(pan * 100))
                self.pan.blockSignals(False)
                self.pan.setEnabled(not pan_auto)
                self.pan.setToolTip(
                    "Automation read: bypass this lane in Automation to adjust manually"
                    if pan_auto
                    else "Stereo balance"
                )
        if self.master:
            self.meter.set_level(float(eng.master_meter.mean()), eng.master_peak)
            reduction = eng.mastering.gain_reduction_db
            self.sub.setText(f"GR {reduction:.1f} dB" if reduction > 0.05 else "-1 dBFS")
        else:
            self.meter.set_level(float(eng.meters[self.index]), float(eng.peaks[self.index]))


class MixerPanel(WindowClient, QWidget):
    changed = Signal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.selected = 0

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The strips scroll horizontally so the rack keeps its width in a
        # narrow window instead of being pushed off the edge.
        board = QWidget()
        lay = QHBoxLayout(board)
        lay.setContentsMargins(14, 14, 8, 14)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.strips = []
        for i in range(NTRACKS):
            strip = Strip(app, i)
            strip.changed.connect(self.changed.emit)
            strip.picked.connect(self.select_track)
            self.strips.append(strip)
            lay.addWidget(strip)
        lay.addStretch(1)

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        scroller.setWidget(board)
        outer.addWidget(scroller, 1)

        # The master never scrolls out of reach: it is pinned beside the rack
        # while the eight track strips scroll behind it.
        master_holder = QWidget()
        master_lay = QVBoxLayout(master_holder)
        master_lay.setContentsMargins(0, 14, 12, 14)
        master_lay.setAlignment(Qt.AlignTop)
        self.master_strip = Strip(app, -1, master=True)
        self.master_strip.changed.connect(self.changed.emit)
        master_lay.addWidget(self.master_strip)
        outer.addWidget(master_holder)

        self.rack = FXRack(app)
        self.rack.changed.connect(self._rack_changed)
        outer.addWidget(self.rack)

        self.select_track(0)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._meters)
        self._timer.start(50)

    def select_track(self, index: int):
        self.selected = max(0, min(NTRACKS - 1, int(index)))
        for i, strip in enumerate(self.strips):
            strip.set_selected(i == self.selected)
        self.rack.set_track(self.selected)

    def _rack_changed(self):
        self.strips[self.rack.index].sync()
        self.changed.emit()

    def _meters(self):
        if not self.isVisible():
            return
        for s in self.strips:
            s.update_meter()
        self.master_strip.update_meter()

    def sync(self):
        for s in self.strips:
            s.sync()
        self.master_strip.sync()
        self.rack.rebuild()
