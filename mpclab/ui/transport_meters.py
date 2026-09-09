"""Small live meters using published engine telemetry, without audio processing."""

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QSizePolicy

from . import theme


class TransportMeters(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(250)
        self.setMaximumWidth(420)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Output, input and DSP meters. Activate to reset peak hold.")
        self.setToolTip(
            "L / R: output RMS, −60 to 0 dBFS. PEAK: held output sample peak.\n"
            "IN: recording input peak; idle when no input is recording.\n"
            "DSP: audio callback load. Click or press Enter to reset peak hold."
        )
        self.levels = (0.0, 0.0)
        self.input_peak = 0.0
        self.input_active = False
        self.cpu = 0.0
        self.held_peak = 0.0

    @staticmethod
    def position(value):
        return max(0.0, min(1.0, (20 * math.log10(max(1e-6, value)) + 60) / 60))

    @staticmethod
    def db_text(value):
        return "−∞" if value <= 1e-6 else f"{20 * math.log10(value):.1f}"

    def set_levels(self, left, right, peak, input_peak, input_active, cpu):
        self.levels = (max(0.0, float(left)), max(0.0, float(right)))
        self.held_peak = max(self.held_peak, float(peak))
        self.input_active = bool(input_active)
        self.input_peak = max(0.0, float(input_peak)) if input_active else 0.0
        self.cpu = max(0.0, float(cpu))
        self.setAccessibleDescription(
            f"Left {self.db_text(self.levels[0])}, right {self.db_text(self.levels[1])}, "
            f"held peak {self.db_text(self.held_peak)} dBFS; "
            f"input {self.db_text(self.input_peak) if input_active else 'idle'}; "
            f"DSP {self.cpu:.0%}"
        )
        self.update()

    def reset_peak(self):
        self.held_peak = 0.0
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.reset_peak()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.reset_peak()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(theme.mono_font(7.5))
        # Stereo pair, then input and callback load; all remain visible when narrow.
        width = self.width()
        stereo_width = width * 0.48
        aux_x = stereo_width + 12
        aux_width = width - aux_x - 2

        def bar(x, y, w, value, linear=False):
            amount = min(1.0, value) if linear else self.position(value)
            p.fillRect(QRectF(x, y, w, 5), theme.q("line"))
            for lo, hi, color in ((0, 0.75, "ok"), (0.75, 0.95, "meter_mid"), (0.95, 1, "rec")):
                end = min(hi, amount)
                if end > lo:
                    p.fillRect(QRectF(x + w * lo, y, w * (end - lo), 5), theme.q(color))

        for index, name in enumerate(("L", "R")):
            p.setPen(theme.q("dim"))
            p.drawText(QRectF(0, index * 12, 12, 12), Qt.AlignVCenter, name)
            bar(15, index * 12 + 4, stereo_width - 15, self.levels[index])
        p.setPen(theme.q("rec" if self.held_peak >= 1.0 else "dim"))
        p.drawText(
            QRectF(0, 27, stereo_width, 14),
            Qt.AlignVCenter,
            f"{'CLIP' if self.held_peak >= 1.0 else 'PEAK'} {self.db_text(self.held_peak)} dBFS",
        )
        p.setPen(theme.q("dim"))
        p.drawText(
            QRectF(aux_x, 0, aux_width, 12),
            Qt.AlignVCenter,
            f"IN {self.db_text(self.input_peak) + ' dB' if self.input_active else 'idle'}",
        )
        bar(aux_x, 14, aux_width, self.input_peak)
        p.setPen(theme.q("dim"))
        p.drawText(QRectF(aux_x, 23, 65, 14), Qt.AlignVCenter, f"DSP {self.cpu:.0%}")
        bar(aux_x + 65, 28, max(10, aux_width - 65), self.cpu, linear=True)
        if self.hasFocus():
            p.setPen(theme.q("accent"))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()
