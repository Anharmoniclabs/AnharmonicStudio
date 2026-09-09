"""Pattern note editor for the built-in instrument."""

from dataclasses import replace

from .window_client import WindowClient

from PySide6.QtCore import Qt, QRectF, Signal, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QScrollArea,
    QCheckBox,
)

from ..music import Note
from .theme import q
from .editor_tools import editor_bar
from .sample_drag import SoundDropFilter

KEY_W, ROW_H, RULER_H = 64, 20, 28
NOTE_NAMES = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")


class PianoCanvas(WindowClient, QWidget):
    selectionChanged = Signal()

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.app = panel.app
        self.selected = set()
        self.clipboard = []
        self.px_per_beat = 88
        self.drag = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Piano roll notes")
        self.setToolTip(
            "Click to draw • drag to move • drag right edge to resize • right-click to erase\n"
            "Ctrl-click selects multiple notes • wheel on a note changes velocity"
        )

    def pattern(self):
        return self.app.project.pattern()

    def visible_indices(self):
        return {
            i for i, note in enumerate(self.pattern().notes) if note.pad == self.panel.target_pad
        }

    def refresh(self):
        self.selected &= self.visible_indices()
        self.setMinimumSize(
            int(KEY_W + self.pattern().length_beats * self.px_per_beat + 24), RULER_H + 128 * ROW_H
        )
        self.update()

    def rect_for(self, note):
        return QRectF(
            KEY_W + note.start * self.px_per_beat,
            RULER_H + (127 - note.pitch) * ROW_H + 2,
            max(5, note.duration * self.px_per_beat - 2),
            ROW_H - 4,
        )

    def hit(self, pos):
        return next(
            (
                i
                for i in reversed(range(len(self.pattern().notes)))
                if self.pattern().notes[i].pad == self.panel.target_pad
                and self.rect_for(self.pattern().notes[i]).contains(pos)
            ),
            None,
        )

    def commit(self):
        self.app._set_dirty(True)
        self.refresh()
        self.selectionChanged.emit()

    def snap(self, beat):
        step = self.panel.snap.currentData()
        return round(beat / step) * step

    def mousePressEvent(self, event):
        self.setFocus()
        pos = event.position()
        pitch = max(0, min(127, 127 - int((pos.y() - RULER_H) / ROW_H)))
        if pos.y() < self.panel.scroll.verticalScrollBar().value() + RULER_H:
            return
        if pos.x() < KEY_W:
            if event.button() == Qt.LeftButton:
                self.app.play_selected_note(pitch, 0.8)
                self.drag = ("audition", pitch)
            return
        i = self.hit(pos)
        if event.button() == Qt.RightButton:
            if i is not None:
                self.app.snapshot()
                del self.pattern().notes[i]
                self.selected.clear()
                self.commit()
            return
        if event.button() != Qt.LeftButton:
            return
        if i is not None and event.modifiers() & Qt.ControlModifier:
            self.selected.symmetric_difference_update({i})
            self.selectionChanged.emit()
            self.update()
            return
        if i is None:
            beat = max(0, self.snap((pos.x() - KEY_W) / self.px_per_beat))
            if beat >= self.pattern().length_beats:
                return
            self.app.snapshot()
            duration = min(self.panel.duration.value(), self.pattern().length_beats - beat)
            self.pattern().notes.append(
                Note(
                    pitch, beat, duration, self.panel.velocity.value() / 100, self.panel.target_pad
                )
            )
            self.selected = {len(self.pattern().notes) - 1}
            self.commit()
            return
        if i not in self.selected:
            self.selected = {i}
        self.app.snapshot()
        mode = "resize" if pos.x() > self.rect_for(self.pattern().notes[i]).right() - 8 else "move"
        self.drag = (mode, pos, {j: replace(self.pattern().notes[j]) for j in self.selected})
        self.selectionChanged.emit()
        self.update()

    def mouseMoveEvent(self, event):
        if not self.drag or self.drag[0] == "audition":
            return
        mode, origin, notes = self.drag
        delta = self.snap((event.position().x() - origin.x()) / self.px_per_beat)
        semitones = -round((event.position().y() - origin.y()) / ROW_H)
        length = self.pattern().length_beats
        if mode == "move":
            delta = max(
                -min(n.start for n in notes.values()),
                min(delta, min(length - n.start - n.duration for n in notes.values())),
            )
            semitones = max(
                -min(n.pitch for n in notes.values()),
                min(semitones, 127 - max(n.pitch for n in notes.values())),
            )
        for i, original in notes.items():
            note = self.pattern().notes[i]
            if mode == "resize":
                note.duration = max(
                    self.panel.snap.currentData(),
                    min(length - note.start, original.duration + delta),
                )
            else:
                note.start = original.start + delta
                note.pitch = original.pitch + semitones
        self.commit()

    def mouseReleaseEvent(self, event):
        if self.drag and self.drag[0] == "audition":
            self.app.release_selected_note(self.drag[1])
        self.drag = None

    def wheelEvent(self, event):
        i = self.hit(event.position())
        if i is None:
            event.ignore()
            return
        self.app.snapshot()
        note = self.pattern().notes[i]
        note.velocity = min(
            1, max(0.01, note.velocity + (0.05 if event.angleDelta().y() > 0 else -0.05))
        )
        self.selected = {i}
        self.commit()

    def delete_selected(self):
        if self.selected:
            self.app.snapshot()
            self.pattern().notes = [
                n for i, n in enumerate(self.pattern().notes) if i not in self.selected
            ]
            self.selected.clear()
            self.commit()

    def keyPressEvent(self, event):
        key, ctrl = event.key(), bool(event.modifiers() & Qt.ControlModifier)
        if ctrl and key == Qt.Key_A:
            self.selected = self.visible_indices()
            self.selectionChanged.emit()
            self.update()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif ctrl and key == Qt.Key_C:
            self.clipboard = [replace(self.pattern().notes[i]) for i in sorted(self.selected)]
        elif ctrl and key == Qt.Key_V and self.clipboard:
            self.app.snapshot()
            origin = min(n.start for n in self.clipboard)
            destination = max(0, self.snap(self.app.engine.beat % self.pattern().length_beats))
            self.selected.clear()
            for n in self.clipboard:
                start = destination + n.start - origin
                if start < self.pattern().length_beats:
                    self.selected.add(len(self.pattern().notes))
                    self.pattern().notes.append(
                        replace(
                            n,
                            start=start,
                            pad=self.panel.target_pad,
                            duration=min(n.duration, self.pattern().length_beats - start),
                        )
                    )
            self.commit()
        elif key in (Qt.Key_Up, Qt.Key_Down) and self.selected:
            self.app.snapshot()
            delta = (12 if event.modifiers() & Qt.ShiftModifier else 1) * (
                1 if key == Qt.Key_Up else -1
            )
            for i in self.selected:
                n = self.pattern().notes[i]
                n.pitch = max(0, min(127, n.pitch + delta))
            self.commit()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), q("canvas"))
        first = max(0, (event.rect().top() - RULER_H) // ROW_H)
        last = min(128, (event.rect().bottom() - RULER_H) // ROW_H + 1)
        for row in range(first, last):
            pitch, y = 127 - row, RULER_H + row * ROW_H
            black = pitch % 12 in (1, 3, 6, 8, 10)
            painter.fillRect(0, y, self.width(), ROW_H, q("bg") if black else q("canvas"))
            painter.fillRect(0, y, KEY_W - 1, ROW_H - 1, q("bg3") if black else q("cell_beat"))
            painter.setPen(q("dim") if black else q("fg"))
            painter.drawText(
                QRectF(4, y, KEY_W - 9, ROW_H),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}",
            )
            painter.setPen(q("line"))
            painter.drawLine(KEY_W, y + ROW_H - 1, self.width(), y + ROW_H - 1)
        step = self.panel.snap.currentData()
        for tick in range(int(self.pattern().length_beats / step) + 1):
            beat = tick * step
            x = int(KEY_W + beat * self.px_per_beat)
            painter.setPen(q("cell_bar") if beat % 4 == 0 else q("line"))
            painter.drawLine(x, event.rect().top(), x, event.rect().bottom())
        painter.setRenderHint(QPainter.Antialiasing)
        for i, note in enumerate(self.pattern().notes):
            if note.pad != self.panel.target_pad:
                continue
            rect = self.rect_for(note)
            if not rect.intersects(QRectF(event.rect())):
                continue
            painter.setBrush(q("accent2") if i in self.selected else q("accent"))
            painter.setPen(QPen(q("fg") if i in self.selected else q("accent_hi"), 1))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(q("on_accent"))
            painter.drawText(
                rect.adjusted(5, 0, -4, -2),
                Qt.AlignVCenter,
                f"{NOTE_NAMES[note.pitch % 12]}{note.pitch // 12 - 1}",
            )
            painter.fillRect(
                QRectF(rect.left() + 2, rect.bottom() - 3, (rect.width() - 4) * note.velocity, 2),
                q("on_accent"),
            )
        if self.app.engine.playing and self.app.engine.mode == "pattern":
            x = KEY_W + self.app.engine.beat % self.pattern().length_beats * self.px_per_beat
            painter.setPen(QPen(q("ok"), 2))
            painter.drawLine(int(x), event.rect().top(), int(x), event.rect().bottom())
        # Sticky bar ruler remains visible when the keyboard is scrolled vertically.
        top = self.panel.scroll.verticalScrollBar().value()
        painter.fillRect(0, top, self.width(), RULER_H, q("bg3"))
        painter.setPen(q("fg"))
        for bar in range(self.pattern().bars):
            painter.drawText(int(KEY_W + bar * 4 * self.px_per_beat + 6), top + 19, str(bar + 1))
        painter.end()


class PianoRollPanel(WindowClient, QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.target_pad = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)
        self.heading = QLabel("PIANO ROLL")
        self.heading.setObjectName("workspaceTitle")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        sound = QHBoxLayout()
        sound.addWidget(QLabel("Sound"))
        self.channel = QComboBox()
        self.channel.setMinimumWidth(190)
        self.channel.setAccessibleName("Piano roll sound channel")
        self.channel.currentIndexChanged.connect(self._channel_changed)
        sound.addWidget(self.channel)
        self.root_note = QSpinBox()
        self.root_note.setRange(0, 127)
        self.root_note.setAccessibleName("Sample root MIDI note")
        self.root_note.setToolTip(
            "Recorded source pitch: 60 = C4. Repitch changes pitch AND duration."
        )
        self.root_note.valueChanged.connect(self._root_changed)
        sound.addWidget(QLabel("Root MIDI"))
        sound.addWidget(self.root_note)
        self.mono = QCheckBox("Mono")
        self.mono.setToolTip("One pitched note at a time for this sound; no glide")
        self.mono.toggled.connect(self._mono_changed)
        sound.addWidget(self.mono)
        edit_sound = QPushButton("Edit sound")
        edit_sound.clicked.connect(self.edit_sound)
        sound.addWidget(edit_sound)
        sound.addStretch()
        layout.addWidget(editor_bar(sound))
        tools = QHBoxLayout()
        self.pattern_box = QComboBox()
        self.pattern_box.setMinimumWidth(140)
        self.pattern_box.currentIndexChanged.connect(self.pick_pattern)
        tools.addWidget(self.pattern_box)
        self.arrange_button = QPushButton("Add to Song")
        self.arrange_button.setToolTip("Place this pattern's notes and steps on the song timeline")
        self.arrange_button.clicked.connect(lambda: self.app.append_pattern_to_arrangement())
        tools.addWidget(self.arrange_button)
        self.snap = QComboBox()
        for label, beats in (("1/4", 1.0), ("1/8", 0.5), ("1/16", 0.25), ("1/32", 0.125)):
            self.snap.addItem(label, beats)
        self.snap.setCurrentIndex(2)
        tools.addWidget(QLabel("Snap"))
        tools.addWidget(self.snap)
        quantize = QPushButton("Quantize")
        quantize.clicked.connect(self.quantize)
        tools.addWidget(quantize)
        self.chord = QComboBox()
        for title, intervals in (
            ("Major", (0, 4, 7)),
            ("Minor", (0, 3, 7)),
            ("Major 7", (0, 4, 7, 11)),
            ("Minor 7", (0, 3, 7, 10)),
        ):
            self.chord.addItem(title, intervals)
        tools.addWidget(self.chord)
        add_chord = QPushButton("Add chord")
        add_chord.clicked.connect(self.add_chord)
        tools.addWidget(add_chord)
        fit = QPushButton("Fit pattern")
        fit.clicked.connect(self.fit_pattern)
        tools.addWidget(fit)
        tools.addStretch()
        layout.addWidget(editor_bar(tools))
        self.canvas = PianoCanvas(self)
        self._sound_drop = SoundDropFilter(app, self.channel, "notes", lambda pos: self.target_pad)
        self._new_sound_drop = SoundDropFilter(app, self.canvas, "notes", lambda pos: None)
        self.channel.setToolTip(
            "Drop to replace this sample sound; drop on the grid to add a new instrument"
        )
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        inspector = QHBoxLayout()
        self.pitch = QSpinBox()
        self.pitch.setRange(0, 127)
        self.pitch.setValue(60)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.03125, 32)
        self.duration.setDecimals(3)
        self.duration.setSingleStep(0.25)
        self.duration.setValue(1)
        self.velocity = QSpinBox()
        self.velocity.setRange(1, 100)
        self.velocity.setValue(80)
        for name, control in (
            ("Pitch (MIDI)", self.pitch),
            ("Length (beats)", self.duration),
            ("Velocity %", self.velocity),
        ):
            inspector.addWidget(QLabel(name))
            inspector.addWidget(control)
            control.setAccessibleName(name)
            control.valueChanged.connect(self.edit_selected)
        delete = QPushButton("Delete notes")
        delete.clicked.connect(self.canvas.delete_selected)
        inspector.addWidget(delete)
        inspector.addStretch()
        layout.addWidget(editor_bar(inspector))
        hint = QLabel(
            "DRAW · click    MOVE · drag    LENGTH · right edge    SELECT · Ctrl-click    "
            "COPY / PASTE · Ctrl+C / V    TRANSPOSE · ↑ ↓"
        )
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)
        self.canvas.selectionChanged.connect(self.sync_selection)
        self.snap.currentIndexChanged.connect(self.canvas.update)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.canvas.update() if self.isVisible() else None)
        self.timer.start(50)
        self.sync()
        QTimer.singleShot(0, self, self.focus_sound)

    def sync(self):
        if self.canvas.drag and self.canvas.drag[0] == "audition":
            self.app.release_selected_note(self.canvas.drag[1])
        self.canvas.selected.clear()
        self.canvas.drag = None
        self.pattern_box.blockSignals(True)
        self.pattern_box.clear()
        for pattern in self.app.project.patterns:
            self.pattern_box.addItem(pattern.name, pattern.id)
        self.pattern_box.setCurrentIndex(
            max(0, self.pattern_box.findData(self.app.project.pattern().id))
        )
        self.pattern_box.blockSignals(False)
        self.sync_channels()
        self.canvas.refresh()

    def sync_channels(self):
        project = self.app.project
        referenced = {n.pad for p in project.patterns for n in p.notes if n.pad is not None}
        self.channel.blockSignals(True)
        self.channel.clear()
        self.channel.addItem(f"Synth · {project.synth.name}", None)
        for index, pad in enumerate(project.pads):
            if not pad.empty or index in referenced:
                label = f"{chr(65 + index // 16)}{index % 16 + 1} · {pad.name or 'Missing sound'}"
                self.channel.addItem(label, index)
        selected = self.channel.findData(self.target_pad)
        if selected < 0:
            selected, self.target_pad = 0, None
        self.channel.setCurrentIndex(selected)
        self.channel.blockSignals(False)
        pad = project.pads[self.target_pad] if self.target_pad is not None else None
        for control, value in (
            (self.root_note, pad.root_note if pad else 60),
            (self.mono, pad.mono if pad else False),
        ):
            control.blockSignals(True)
            control.setEnabled(pad is not None)
            (control.setChecked if control is self.mono else control.setValue)(value)
            control.blockSignals(False)
        route = f" → {project.tracks[pad.track].name}" if pad else ""
        self.heading.setText(f"PIANO ROLL / {self.channel.currentText()}{route}")
        if getattr(self.app, "typing_keyboard", None) is not None:
            self.app.typing_keyboard.sync()

    def select_channel(self, index):
        self.target_pad = index
        self.canvas.selected.clear()
        if self.canvas.drag and self.canvas.drag[0] == "audition":
            self.app.release_selected_note(self.canvas.drag[1])
        self.canvas.drag = None
        self.sync_channels()
        self.canvas.refresh()
        self.focus_sound()
        QTimer.singleShot(0, self, self.focus_sound)

    def focus_sound(self):
        """Keep this channel's notes (or its source root) in the visible octave."""
        pitches = [n.pitch for n in self.canvas.pattern().notes if n.pad == self.target_pad]
        root = (
            self.app.project.pads[self.target_pad].root_note if self.target_pad is not None else 60
        )
        centre = (min(pitches) + max(pitches)) / 2 if pitches else root
        self.scroll.verticalScrollBar().setValue(
            max(
                0,
                round(RULER_H + (127 - centre + 0.5) * ROW_H - self.scroll.viewport().height() / 2),
            )
        )

    def _channel_changed(self, index):
        if index >= 0:
            self.select_channel(self.channel.itemData(index))

    def _root_changed(self, value):
        if self.target_pad is not None:
            self.app.snapshot()
            self.app.project.pads[self.target_pad].root_note = value
            self.app._set_dirty(True)

    def _mono_changed(self, value):
        if self.target_pad is not None:
            self.app.snapshot()
            self.app.project.pads[self.target_pad].mono = value
            self.app._set_dirty(True)

    def edit_sound(self):
        if self.target_pad is None:
            self.app.show_tab(4)
            return
        index = self.target_pad
        pad = self.app.project.pads[index]
        self.app.select_pad(index)
        if pad.sample_id in self.app.library.clips:
            self.app.load_clip_into_editor(pad.sample_id)
            clip = self.app.library.clips[pad.sample_id]
            self.app.wave.set_selection(pad.start, pad.end or clip.duration)
        if self.app.studio.enabled:
            self.app.studio.select(0)
        else:
            self.app.show_tab(0)

    def fit_pattern(self):
        self.canvas.px_per_beat = max(
            12, (self.scroll.viewport().width() - KEY_W - 24) / self.canvas.pattern().length_beats
        )
        self.canvas.refresh()
        self.focus_sound()

    def pick_pattern(self, index):
        if index >= 0:
            self.app.project.current_pattern = self.pattern_box.itemData(index)
            self.app._sync_pattern_controls()
            self.app._refresh_place_box()

    def sync_selection(self):
        if len(self.canvas.selected) != 1:
            return
        note = self.canvas.pattern().notes[next(iter(self.canvas.selected))]
        for control, value in (
            (self.pitch, note.pitch),
            (self.duration, note.duration),
            (self.velocity, round(note.velocity * 100)),
        ):
            control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(False)

    def edit_selected(self):
        if not self.canvas.selected:
            return
        self.app.snapshot()
        sender = self.sender()
        for i in self.canvas.selected:
            note = self.canvas.pattern().notes[i]
            if sender == self.pitch:
                note.pitch = self.pitch.value()
            elif sender == self.duration:
                note.duration = min(
                    self.duration.value(), self.canvas.pattern().length_beats - note.start
                )
            elif sender == self.velocity:
                note.velocity = self.velocity.value() / 100
        self.canvas.commit()

    def quantize(self):
        notes = self.canvas.pattern().notes
        if not notes:
            return
        self.app.snapshot()
        for i in self.canvas.selected or self.canvas.visible_indices():
            n = notes[i]
            n.start = min(
                self.canvas.pattern().length_beats - self.snap.currentData(),
                max(0, self.canvas.snap(n.start)),
            )
            n.duration = min(n.duration, self.canvas.pattern().length_beats - n.start)
        self.canvas.commit()

    def add_chord(self):
        self.app.snapshot()
        pat = self.canvas.pattern()
        start = self.canvas.snap(self.app.engine.beat % pat.length_beats)
        start = min(start, pat.length_beats - self.snap.currentData())
        self.canvas.selected.clear()
        for interval in self.chord.currentData():
            pitch = self.pitch.value() + interval
            if pitch <= 127:
                self.canvas.selected.add(len(pat.notes))
                pat.notes.append(
                    Note(
                        pitch,
                        start,
                        min(self.duration.value(), pat.length_beats - start),
                        self.velocity.value() / 100,
                        self.target_pad,
                    )
                )
        self.canvas.commit()
