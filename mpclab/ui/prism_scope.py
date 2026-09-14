"""Rendered synth waveform, spectrum and ADSR preview; bounded off-thread DSP."""

from dataclasses import asdict, replace
import threading

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QTimer, Signal, Qt
from PySide6.QtGui import QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..synth import SynthVoice
from .theme import q
from .window_client import WindowClient, emit_if_alive


class PrismScope(WindowClient, QWidget):
    ready = Signal(int, object)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setMinimumHeight(130)
        self.setMaximumHeight(180)
        self.setAccessibleName("Rendered synth waveform and spectrum")
        self._signature = None
        self._generation = 0
        self._busy = False
        self._patch = None
        self._data = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render)
        self.ready.connect(self._received)

    def set_patch(self, patch):
        signature = asdict(patch)
        if signature == self._signature:
            return
        self._signature = signature
        self._patch = replace(patch)
        self._generation += 1
        self._data = None
        if not patch.sample_source:
            self._timer.start(180)
        self.update()

    def _render(self):
        if self._busy or self._patch is None or self._patch.sample_source:
            return
        self._busy = True
        generation, patch = self._generation, replace(self._patch)

        def work():
            result = None
            try:
                audio = np.zeros((8192, 2), np.float32)
                voice = SynthVoice(60, 0.7, 24000, gate_frames=6000)
                for start in range(0, len(audio), 512):
                    voice.render(audio[start : start + 512], patch)
                mono = audio.mean(axis=1)
                # Peak envelope retains narrow transients when fitting the whole note.
                envelope = np.max(np.abs(mono.reshape(512, 16)), axis=1)
                section = mono[2048:4096]
                spectrum = np.abs(np.fft.rfft(section * np.hanning(len(section)))) / 1024
                result = (envelope, np.clip(20 * np.log10(np.maximum(spectrum, 1e-5)), -80, 0))
            finally:
                emit_if_alive(self, "ready", generation, result)

        threading.Thread(target=work, name="prism-tone-preview", daemon=True).start()

    def _received(self, generation, data):
        self._busy = False
        if generation == self._generation:
            self._data = data
            self.update()
        else:
            self._timer.start(180)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), q("canvas"))
        full = QRectF(self.rect()).adjusted(14, 9, -14, -9)
        wave = QRectF(full.left(), full.top() + 24, full.width() * 0.56, full.height() - 29)
        spec = QRectF(
            full.left() + full.width() * 0.62,
            full.top() + 24,
            full.width() * 0.38,
            full.height() - 29,
        )
        p.setPen(q("dim2"))
        p.drawText(
            QRectF(full.left(), full.top(), wave.width(), 20),
            "RENDERED C4 · 341 ms · peak envelope",
        )
        p.drawText(QRectF(spec.left(), full.top(), spec.width(), 20), "SPECTRUM · 30 Hz — 12 kHz")
        p.setPen(QPen(q("line"), 1))
        for i in range(1, 4):
            y = wave.top() + wave.height() * i / 4
            p.drawLine(QPointF(wave.left(), y), QPointF(wave.right(), y))
        if self._data is None:
            p.setPen(q("dim2"))
            p.drawText(wave, Qt.AlignCenter, "Rendering tone preview…")
            return
        env, spectrum = self._data
        p.setPen(QPen(q("accent_hi"), 1))
        for i, value in enumerate(env):
            x = wave.left() + wave.width() * i / 511
            amp = min(1, float(value)) * wave.height() * 0.48
            p.drawLine(QPointF(x, wave.center().y() - amp), QPointF(x, wave.center().y() + amp))
        points = []
        for i in range(180):
            hz = 30 * (12000 / 30) ** (i / 179)
            db = float(spectrum[min(len(spectrum) - 1, round(hz / 24000 * 2048))])
            points.append(
                QPointF(
                    spec.left() + spec.width() * i / 179,
                    spec.bottom() - (db + 80) / 80 * spec.height(),
                )
            )
        p.setPen(QPen(q("accent2"), 2))
        p.drawPolyline(QPolygonF(points))
