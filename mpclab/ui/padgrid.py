"""The 4x4 pad bank and the per-pad inspector."""

from __future__ import annotations

import time

import numpy as np
from .window_client import WindowClient

from PySide6.QtCore import QEvent, Qt, QRectF, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QDoubleSpinBox,
    QSlider,
    QPushButton,
    QFormLayout,
    QScrollArea,
    QFrame,
    QMenu,
    QGridLayout,
)

from ..model import PADS_PER_BANK, DISPLAY_ORDER, PAD_KEYS, MODES
from .theme import q, TRACK_COLORS
from .waveform import draw_peaks, RANGE_MIME


class PadGrid(WindowClient, QWidget):
    padPressed = Signal(int, float)  # global index, velocity
    padReleased = Signal(int)
    padSelected = Signal(int)
    sampleDropped = Signal(int, str)  # global index, clip id
    rangeDropped = Signal(int, str, float, float)  # index, clip id, start, end
    padCleared = Signal(int)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.bank = 0
        self.selected = 0
        self.setAcceptDrops(True)
        self.setMinimumSize(240, 240)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pressed: set[int] = set()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ── helpers ──────────────────────────────────────────────
    def global_index(self, local: int) -> int:
        return self.bank * PADS_PER_BANK + local

    def _cells(self):
        w, h = self.width(), self.height()
        gap = 6
        cw = (w - gap * 5) / 4
        ch = (h - gap * 5) / 4
        for row in range(4):
            for col in range(4):
                local = DISPLAY_ORDER[row * 4 + col]
                x = gap + col * (cw + gap)
                y = gap + row * (ch + gap)
                yield local, QRectF(x, y, cw, ch)

    def _hit(self, pos) -> tuple[int, QRectF] | tuple[None, None]:
        for local, rect in self._cells():
            if rect.contains(pos):
                return local, rect
        return None, None

    def _tick(self):
        # The audio thread only stamps impacts; the GUI owns expiry.  Without
        # pruning, one pad hit leaves this 30 Hz repaint running forever.
        flash = self.app.engine.hit_flash
        now = time.monotonic()
        for pad, stamped in tuple(flash.items()):
            if now - stamped >= 0.16 and flash.get(pad) == stamped:
                flash.pop(pad, None)
        if flash:
            self.update()

    def _audition_from_menu(self, gi: int) -> None:
        """Give gate/loop pads a finite preview from the context menu."""
        self.padPressed.emit(gi, 1.0)
        if self.app.project.pads[gi].mode != "one-shot":
            # Do not let this delayed menu release cut a newer physical hit.
            QTimer.singleShot(
                300, self, lambda: self.padReleased.emit(gi) if gi not in self._pressed else None
            )

    # ── mouse ────────────────────────────────────────────────
    def mousePressEvent(self, ev):
        local, rect = self._hit(ev.position())
        if local is None:
            return
        gi = self.global_index(local)
        self.selected = gi
        self.padSelected.emit(gi)
        if ev.button() == Qt.RightButton:
            menu = QMenu(self)
            pad = self.app.project.pads[gi]
            play = menu.addAction("Audition pad")
            play.setEnabled(not pad.empty)
            play.triggered.connect(lambda: self._audition_from_menu(gi))
            menu.addSeparator()
            clear = menu.addAction("Clear pad (undoable)")
            clear.setEnabled(not pad.empty)
            clear.triggered.connect(lambda: self.padCleared.emit(gi))
            menu.exec(ev.globalPosition().toPoint())
            return
        # Hitting lower on the pad plays quieter, like a real velocity pad.
        rel = (ev.position().y() - rect.top()) / max(1.0, rect.height())
        vel = max(0.35, min(1.0, 1.0 - rel * 0.5))
        if ev.modifiers() & Qt.ShiftModifier:
            vel = 0.5
        self._pressed.add(gi)
        self.padPressed.emit(gi, vel)
        self.update()

    def mouseReleaseEvent(self, ev):
        for gi in list(self._pressed):
            self.padReleased.emit(gi)
        self._pressed.clear()
        self.update()

    # ── drag & drop from the browser and the waveform ────────
    @staticmethod
    def _accepts(mime) -> bool:
        return mime.hasFormat("application/x-mpclab-clip") or mime.hasFormat(RANGE_MIME)

    def dragEnterEvent(self, ev):
        if self._accepts(ev.mimeData()):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if self._accepts(ev.mimeData()):
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        local, _ = self._hit(ev.position())
        if local is None:
            return
        mime = ev.mimeData()
        if mime.hasFormat(RANGE_MIME):
            # A range dragged out of the CHOP editor: clip id, start, end.
            payload = bytes(mime.data(RANGE_MIME)).decode()
            clip_id, start, end = payload.split("|")
            self.rangeDropped.emit(self.global_index(local), clip_id, float(start), float(end))
        else:
            clip_id = bytes(mime.data("application/x-mpclab-clip")).decode()
            self.sampleDropped.emit(self.global_index(local), clip_id)
        ev.acceptProposedAction()
        self.update()

    # ── painting ─────────────────────────────────────────────
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), q("bg2"))
        proj = self.app.project
        now = time.monotonic()
        flash = self.app.engine.hit_flash

        small = QFont(self.font())
        small.setPointSizeF(7.5)
        name_font = QFont(self.font())
        name_font.setPointSizeF(8.0)
        name_metrics = QFontMetricsF(name_font)

        for local, rect in self._cells():
            gi = self.global_index(local)
            pad = proj.pads[gi]
            since_hit = now - flash.get(gi, 0.0)
            lit = since_hit < 0.16
            pulse = max(0.0, 1.0 - since_hit / 0.16) if lit else 0.0

            # A restrained lower edge keeps the pads tactile without glossy
            # faces competing with the names, waveforms and selection.
            shadow = QRectF(rect).translated(0, 1.0)
            p.setPen(Qt.NoPen)
            p.setBrush(q("bg", 190))
            p.drawRoundedRect(shadow, 3, 3)
            p.setBrush(q("accent_hi") if lit else q("pad_empty" if pad.empty else "pad"))
            pen = QPen(
                q("accent2")
                if gi == self.selected
                else (q("pad_lit_line") if lit else q("padline"))
            )
            pen.setWidth(2 if gi == self.selected else 1)
            if pad.empty and not lit:
                pen.setStyle(Qt.DashLine)
                pen.setColor(q("pad_empty_line"))
            p.setPen(pen)
            p.drawRoundedRect(rect, 3, 3)

            # Hardware-style inner rim and a quick bloom on impact.
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(q("edge", 120 if not pad.empty else 70), 1))
            p.drawLine(
                int(rect.left() + 7),
                int(rect.top() + 2),
                int(rect.right() - 7),
                int(rect.top() + 2),
            )
            if lit:
                bloom = q("accent_hi")
                bloom.setAlpha(int(40 + 80 * pulse))
                p.setPen(QPen(bloom, 2.0))
                p.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 2, 2)

            badge = QRectF(rect.right() - 22, rect.bottom() - 17, 17, 13)

            if not pad.empty:
                clip = self.app.library.clips.get(pad.sample_id)
                peaks = self.app.library.peaks(pad.sample_id) if clip else None
                if peaks is not None and clip and clip.duration > 0:
                    a = pad.start / clip.duration
                    b = (pad.end or clip.duration) / clip.duration
                    wave = QRectF(rect.left() + 10, rect.bottom() - 18, rect.width() - 36, 14)
                    p.save()
                    p.setClipRect(wave)
                    draw_peaks(
                        p,
                        peaks,
                        wave,
                        max(0.0, a),
                        min(1.0, b),
                        q("pad_lit_ink", 120) if lit else q("pad_wave", 150),
                    )
                    p.restore()

                p.setFont(name_font)
                p.setPen(q("pad_lit_ink") if lit else q("fg"))
                label = pad.name or (clip.name if clip else "")
                text_width = max(1.0, rect.width() - 15)
                first, separator, second = label.partition("·")
                if not separator:
                    words = label.split()
                    first = words.pop(0) if words else ""
                    while (
                        words
                        and name_metrics.horizontalAdvance(f"{first} {words[0]}") <= text_width
                    ):
                        first += " " + words.pop(0)
                    second = " ".join(words)
                # Use two full-width, elided lines. The bottom strip belongs
                # to the waveform and keycap, including at 60 px pad widths.
                for line, text in enumerate((first.strip(), second.strip())):
                    p.drawText(
                        QRectF(
                            rect.left() + 10,
                            rect.top() + 4 + line * name_metrics.height(),
                            text_width,
                            name_metrics.height(),
                        ),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        name_metrics.elidedText(text, Qt.ElideRight, text_width),
                    )

                if pad.track < len(TRACK_COLORS):
                    p.setBrush(QColor(TRACK_COLORS[pad.track]))
                    p.setPen(Qt.NoPen)
                    p.drawRoundedRect(
                        QRectF(rect.left() + 4, rect.top() + 5, 2, rect.height() - 10), 1, 1
                    )

            p.setFont(small)
            p.setPen(Qt.NoPen)
            p.setBrush(q("accent", 150) if gi == self.selected else q("bg", 105))
            p.drawRoundedRect(badge, 2, 2)
            p.setPen(q("pad_lit_ink") if lit or gi == self.selected else q("dim"))
            p.drawText(badge, Qt.AlignCenter, PAD_KEYS[local])

            if gi == self.selected:
                # Four short corner brackets are clearer than another full
                # border when a pad is also flashing under the finger.
                p.setPen(QPen(q("accent_hi"), 2))
                length = min(11.0, rect.width() * 0.16)
                for x, sx in ((rect.left() + 3, 1), (rect.right() - 3, -1)):
                    p.drawLine(
                        int(x), int(rect.top() + 3), int(x + sx * length), int(rect.top() + 3)
                    )
                    p.drawLine(
                        int(x), int(rect.bottom() - 3), int(x + sx * length), int(rect.bottom() - 3)
                    )


class _PadParameterSpinBox(QDoubleSpinBox):
    """Keep uncommitted typing cancellable without invoking transport Escape."""

    def event(self, event):
        if (
            event.type() == QEvent.ShortcutOverride
            and (event.matches(QKeySequence.Undo) or event.matches(QKeySequence.Redo))
            and not self.lineEdit().isModified()
            and not self.lineEdit().isRedoAvailable()
        ):
            # Once entry is committed, the window's project undo/redo owns
            # these keys. During typing, Qt still owns local text history.
            event.ignore()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.setValue(self.value())
            self.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = self.lineEdit().createStandardContextMenu()
        menu.addSeparator()
        menu.addAction(self.parent().reset_action)
        if hasattr(self.parent(), "mute_action"):
            menu.addAction(self.parent().mute_action)
        menu.exec(event.globalPos())
        menu.deleteLater()


class _PadParameterControl(QWidget):
    """Numeric entry sharing one undo snapshot per slider drag."""

    reset_value = 0.0

    editStarted = Signal()
    valueChanged = Signal(float)

    def __init__(self, value: float, parent=None):
        super().__init__(parent)
        self._value = value
        self._drag_changed = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(*self._slider_range())
        self.slider.setSingleStep(1)
        self.slider.setPageStep(self.page_step)
        self.slider.setValue(self._slider_position(value))
        self.slider.setAccessibleName(self.accessible_name)
        self.editor = _PadParameterSpinBox(self)
        self.editor.setObjectName(self.object_name)
        self.editor.setAccessibleName(self.accessible_name)
        self.editor.setDecimals(self.decimals)
        self.editor.setRange(*self._editor_range())
        self.editor.setSingleStep(1 / self.scale)
        self.editor.setSuffix(self.suffix)
        self.editor.setKeyboardTracking(False)
        self.editor.setValue(self._editor_value(value))
        tip = self.tip
        self.slider.setToolTip(tip)
        self.editor.setToolTip(tip)
        self.reset_action = QAction(self.reset_text, self)
        self.reset_action.setShortcut("Alt+0")
        self.reset_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.reset_action.triggered.connect(lambda: self._change(self.reset_value))
        self.addAction(self.reset_action)
        self.slider.addAction(self.reset_action)
        self.slider.setContextMenuPolicy(Qt.ActionsContextMenu)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.editor)
        self.slider.sliderReleased.connect(self._end_drag)
        self.slider.valueChanged.connect(
            lambda position: self._change(self._slider_value(position), self.slider.isSliderDown())
        )
        self.editor.valueChanged.connect(self._editor_changed)

    def _slider_range(self):
        return -self.limit * self.scale, self.limit * self.scale

    def _editor_range(self):
        return -self.limit, self.limit

    def _slider_position(self, value):
        return round(value * self.scale)

    def _slider_value(self, position):
        return position / self.scale

    def _editor_value(self, value):
        return value

    def _editor_changed(self, value):
        self._change(value)

    def _end_drag(self):
        self._drag_changed = False

    def _change(self, value: float, dragging: bool = False):
        # Synchronizing the widgets must not feed a rounded slider position
        # back into a saved parameter or create another project-history entry.
        with QSignalBlocker(self.slider), QSignalBlocker(self.editor):
            self.slider.setValue(self._slider_position(value))
            self.editor.setValue(self._editor_value(value))
        if value == self._value:
            return
        if not dragging or not self._drag_changed:
            self.editStarted.emit()
        self._drag_changed = dragging
        self._value = value
        self.valueChanged.emit(value)


class PadPitchControl(_PadParameterControl):
    limit, scale, page_step, decimals = 24, 100, 100, 2
    accessible_name = "Pad pitch (semitones)"
    object_name, suffix = "padPitch", " st"
    reset_text = "Reset pitch to 0.00 st"
    tip = (
        "Pitch in semitones; 0.01 st = 1 cent. Applies to the next pad hit.\n"
        "Type a value, then Enter or Tab to apply; Escape cancels typing.\n"
        "Arrow keys: 1 cent. Slider Page Up/Down: 1 semitone.\n"
        "Right-click for Reset pitch, or press Alt+0."
    )


class PadPanControl(_PadParameterControl):
    limit, scale, page_step, decimals = 100, 10, 100, 1
    accessible_name = "Pad pan (negative left, positive right, zero center)"
    object_name, suffix = "padPan", " %"
    reset_text = "Reset pan to center"
    tip = (
        "Pan: -100% left, 0% center, +100% right. Applies to the next pad hit.\n"
        "Type a value, then Enter or Tab to apply; Escape cancels typing.\n"
        "Arrow keys: 0.1%. Slider Page Up/Down: 10%.\n"
        "Right-click for Reset pan to center, or press Alt+0."
    )


class PadGainControl(_PadParameterControl):
    """Keep the linear fader and persisted gain, with a decibel editor."""

    scale, page_step, decimals = 10, 10, 2
    reset_value = 1.0
    accessible_name = "Pad gain (decibels; zero dB is unity)"
    object_name, suffix = "padGain", " dB"
    reset_text = "Reset gain to unity (0 dB)"
    tip = (
        "Gain in dB; 0 dB is unity. Applies to the next pad hit.\n"
        "Type a value, then Enter or Tab to apply; Escape cancels typing.\n"
        "Numeric arrows: 0.1 dB. Slider retains its linear 0–4 range.\n"
        "Numeric floor: -120 dB; one step below is exact mute (-∞).\n"
        "Lower saved gains show < -120 dB and are preserved until edited.\n"
        "Right-click for Mute or Reset gain, or press Alt+0 for unity."
    )

    def __init__(self, value: float, parent=None):
        super().__init__(value, parent)
        self.editor.setSpecialValueText("-∞ dB")
        self.mute_action = QAction("Mute gain (-∞ dB)", self)
        self.mute_action.triggered.connect(lambda: self._change(0.0))
        self.slider.addAction(self.mute_action)

    def _slider_range(self):
        return 0, 1000

    def _editor_range(self):
        return -120.1, 12.04

    def _slider_position(self, value):
        return round(value * 250)

    def _slider_value(self, position):
        return position / 250

    def _editor_value(self, value):
        # A display rebuild never quantizes persisted gain. Very quiet legacy
        # values remain positive even below the numeric editor's floor.
        self.editor.setPrefix("< " if 0 < value < 1e-6 else "")
        return -120.1 if value == 0 else max(-120.0, 20 * np.log10(value))

    def _editor_changed(self, value):
        if value == round(self._editor_value(self._value), self.decimals):
            return
        # The rounded +12.04 dB endpoint represents exactly 4x, not 3.999447x.
        # Fractional steps can land between the floor and mute sentinel;
        # snap those to the floor before allowing a subsequent step to mute.
        gain = (
            0.0
            if value == self.editor.minimum()
            else (4.0 if value == self.editor.maximum() else 10 ** (max(-120.0, value) / 20))
        )
        self._change(gain)


class _PadTimeControl(_PadParameterControl):
    """Millisecond entry over the existing seconds model and linear slider."""

    scale, page_step, decimals = 10, 10, 1
    suffix = " ms"
    slider_steps = 1000

    def _slider_range(self):
        return 0, self.slider_steps

    def _editor_range(self):
        return self.minimum_ms, self.maximum_ms

    def _slider_position(self, value):
        return round(
            (value * 1000 - self.minimum_ms)
            / (self.maximum_ms - self.minimum_ms)
            * self.slider_steps
        )

    def _slider_value(self, position):
        return (
            self.minimum_ms + position / self.slider_steps * (self.maximum_ms - self.minimum_ms)
        ) / 1000

    def _editor_value(self, value):
        milliseconds = value * 1000
        # Legacy projects may contain times outside the current control range.
        # Show the bound honestly without changing the stored articulation.
        self.editor.setPrefix(
            "< "
            if milliseconds < self.minimum_ms
            else "> "
            if milliseconds > self.maximum_ms
            else ""
        )
        return milliseconds

    def _editor_changed(self, value):
        # Re-entering the rounded display must not quantize saved seconds.
        displayed = round(
            max(self.minimum_ms, min(self.maximum_ms, self._editor_value(self._value))),
            self.decimals,
        )
        if value != displayed:
            self._change(value / 1000)


class PadAttackControl(_PadTimeControl):
    minimum_ms, maximum_ms, reset_value = 0.5, 400.0, 0.002
    accessible_name = "Pad attack (milliseconds)"
    object_name = "padAttack"
    reset_text = "Reset attack to 2.0 ms"
    tip = (
        "Attack: fade in over 0.5–400 ms. Applies to the next pad hit.\n"
        "Type a value, then Enter or Tab to apply; Escape cancels typing.\n"
        "Numeric arrows: 0.1 ms. Slider retains its linear range.\n"
        "Right-click for Reset attack to 2.0 ms, or press Alt+0."
    )


class PadReleaseControl(_PadTimeControl):
    minimum_ms, maximum_ms, reset_value = 5.0, 1500.0, 0.03
    accessible_name = "Pad release (milliseconds)"
    object_name = "padRelease"
    reset_text = "Reset release to 30.0 ms"
    tip = (
        "Release: fade out over 5–1500 ms at the slice end or gate/loop note-off.\n"
        "Applies to the next pad hit; playback stays inside the selected slice.\n"
        "Type a value, then Enter or Tab to apply; Escape cancels typing.\n"
        "Numeric arrows: 0.1 ms. Slider retains its linear range.\n"
        "Right-click for Reset release to 30.0 ms, or press Alt+0."
    )


class PadLoopCrossfadeControl(_PadTimeControl):
    minimum_ms, maximum_ms, reset_value = 0.0, 50.0, 0.005
    slider_steps = 500
    accessible_name = "Pad loop crossfade (milliseconds; zero disables)"
    object_name = "padLoopCrossfade"
    reset_text = "Reset loop crossfade to 5.0 ms"
    tip = (
        "Loop crossfade: blend the source tail/head over 0–50 ms; 0 disables it.\n"
        "Used in Loop mode on the next hit, capped below half the source slice.\n"
        "Pitch and tempo repitch also change the audible crossfade duration.\n"
        "Type a value, then Enter or Tab to apply; Escape cancels typing.\n"
        "Numeric arrows: 0.1 ms. Right-click for Reset to 5.0 ms, or press Alt+0."
    )


class PadInspector(WindowClient, QScrollArea):
    """Parameter editor for the selected pad."""

    changed = Signal()
    editSample = Signal(str)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.index = 0
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._body = QWidget()
        self.setWidget(self._body)
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(9, 9, 9, 9)
        self._layout.setSpacing(6)
        self._building = False
        self.rebuild()

    def set_pad(self, index: int):
        self.index = index
        self.rebuild()

    def _clear(self):
        # deleteLater() alone leaves the widget parented and still painting
        # until the event loop runs, which leaves ghost text behind.
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                # Rebuilding after undo, project load or pad selection retires
                # the old editor. Its focus-out must not commit pending text
                # into a detached pad or snapshot the replacement project.
                for control in w.findChildren(_PadParameterControl):
                    control.blockSignals(True)
                for slider in w.findChildren(QSlider):
                    slider.blockSignals(True)
                w.setParent(None)
                w.deleteLater()

    @staticmethod
    def _lbl(text):
        lab = QLabel(text)
        lab.setObjectName("hint")
        return lab

    def rebuild(self):
        self._building = True
        self._clear()
        proj = self.app.project
        gi = self.index
        pad = proj.pads[gi]
        bank = chr(ord("A") + gi // PADS_PER_BANK)
        title = QLabel(f"PAD {bank}{gi % PADS_PER_BANK + 1}")
        title.setObjectName("title")
        self._layout.addWidget(title)

        if pad.empty:
            hint = QLabel(
                "Drag a sample from the browser onto a pad,\nor chop a sample and press → PADS."
            )
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            self._layout.addWidget(hint)
            self._layout.addStretch(1)
            self._building = False
            return

        clip = self.app.library.clips.get(pad.sample_id)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(5)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        name = QLineEdit(pad.name or (clip.name if clip else ""))
        name.textChanged.connect(lambda t: (setattr(pad, "name", t), self.changed.emit()))
        form.addRow(self._lbl("NAME"), name)

        dur = (pad.end or (clip.duration if clip else 0.0)) - pad.start
        info = QLabel(
            f"{pad.start:.3f}s → {(pad.end or (clip.duration if clip else 0)):.3f}s   ({dur:.3f}s)"
        )
        info.setObjectName("hint")
        form.addRow(self._lbl("SLICE"), info)

        gain = PadGainControl(pad.gain, self._body)
        gain.editStarted.connect(self.app.snapshot)
        gain.valueChanged.connect(lambda v: (setattr(pad, "gain", v), self.changed.emit()))
        form.addRow(self._lbl("GAIN"), gain)
        pan = PadPanControl(pad.pan * 100, self._body)
        pan.editStarted.connect(self.app.snapshot)
        pan.valueChanged.connect(lambda v: (setattr(pad, "pan", v / 100), self.changed.emit()))
        form.addRow(self._lbl("PAN L/R"), pan)
        pitch = PadPitchControl(pad.pitch, self._body)
        pitch.editStarted.connect(self.app.snapshot)
        pitch.valueChanged.connect(lambda v: (setattr(pad, "pitch", v), self.changed.emit()))
        form.addRow(self._lbl("PITCH"), pitch)
        sync = QComboBox()
        sync.setObjectName("padTempoSync")
        sync.addItem("Free playback", 0.0)
        for beats in sorted({0.5, 1.0, 2.0, 4.0, 8.0, 16.0, pad.sync_beats} - {0.0}):
            sync.addItem(f"{beats:g} beats · repitch", beats)
        sync.setCurrentIndex(sync.findData(pad.sync_beats))
        sync.setToolTip(
            "Fit this range to song beats by changing playback speed and pitch. "
            "Pitch adds transposition on top. Applies on the next hit; Free disables tempo sync."
        )

        def set_sync(index):
            if self._building or self.app.project is not proj or self.index != gi:
                return
            if self.app.project.pads[gi] is not pad or not self._body.isAncestorOf(sync):
                return
            value = sync.itemData(index)
            if value is not None and value != pad.sync_beats:
                self.app.snapshot()
                pad.sync_beats = value
                self.changed.emit()

        sync.currentIndexChanged.connect(set_sync)
        form.addRow(self._lbl("TEMPO"), sync)
        for label, attribute, control_type in (
            ("ATTACK", "attack", PadAttackControl),
            ("RELEASE", "release", PadReleaseControl),
            ("LOOP XFADE", "loop_crossfade", PadLoopCrossfadeControl),
        ):
            control = control_type(getattr(pad, attribute), self._body)
            control.editStarted.connect(self.app.snapshot)
            control.valueChanged.connect(
                lambda v, attr=attribute: (setattr(pad, attr, v), self.changed.emit())
            )
            form.addRow(self._lbl(label), control)

        mode = QComboBox()
        mode.addItems(MODES)
        mode.setCurrentText(pad.mode)
        mode.currentTextChanged.connect(lambda t: (setattr(pad, "mode", t), self.changed.emit()))
        form.addRow(self._lbl("MODE"), mode)

        choke = QComboBox()
        choke.addItems(["none"] + [f"group {i}" for i in range(1, 9)])
        choke.setCurrentIndex(pad.choke)
        choke.currentIndexChanged.connect(lambda i: (setattr(pad, "choke", i), self.changed.emit()))
        form.addRow(self._lbl("CHOKE"), choke)

        out = QComboBox()
        out.addItems(
            [
                f"{i + 1} · {t.name}" + ("  ·  fx" if t.fx.active or t.fx.sends_active else "")
                for i, t in enumerate(proj.tracks)
            ]
        )
        out.setCurrentIndex(pad.track)
        out.setToolTip(
            "Which mixer track this pad plays through —\n"
            "'fx' marks a track that has an effect chain on it"
        )
        out.currentIndexChanged.connect(lambda i: (setattr(pad, "track", i), self.changed.emit()))
        form.addRow(self._lbl("OUT"), out)

        holder = QWidget()
        holder.setLayout(form)
        self._layout.addWidget(holder)

        buttons = QWidget()
        hb = QGridLayout(buttons)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(5)
        rev = QPushButton("REVERSE")
        rev.setObjectName("mini")
        rev.setCheckable(True)
        rev.setChecked(pad.reverse)
        rev.toggled.connect(lambda b: (setattr(pad, "reverse", b), self.changed.emit()))
        normalise = QPushButton("NORMALIZE")
        normalise.setObjectName("mini")
        normalise.setToolTip("Set this slice peak to -1 dBFS without changing its audio")
        normalise.clicked.connect(lambda: self.app.normalize_pad(gi))
        tighten = QPushButton("TIGHTEN")
        tighten.setObjectName("mini")
        tighten.setToolTip("Trim near-silence from this pad's start and end")
        tighten.clicked.connect(lambda: self.app.tighten_pad(gi))
        edit = QPushButton("EDIT SAMPLE")
        edit.setObjectName("mini")
        edit.clicked.connect(lambda: self.editSample.emit(pad.sample_id))
        hb.addWidget(rev, 0, 0)
        hb.addWidget(edit, 0, 1)
        hb.addWidget(normalise, 1, 0)
        hb.addWidget(tighten, 1, 1)
        self._layout.addWidget(buttons)
        self._layout.addStretch(1)
        self._building = False
