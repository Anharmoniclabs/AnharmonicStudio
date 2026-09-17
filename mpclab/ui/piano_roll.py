"""Pattern note editor for the built-in instrument."""

from dataclasses import replace
import math
from pathlib import Path

from .window_client import WindowClient

from PySide6.QtCore import Qt, QRectF, Signal, QTimer, QEvent
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
    QMenu,
    QDialog,
    QFormLayout,
    QDialogButtonBox,
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
        self.tool = "draw"
        self.marquee = None
        self.cursor_beat = 0.0
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Piano roll notes")
        self.setAccessibleDescription(
            "Draw and edit notes. Arrow keys move selected notes: left and right move in time, "
            "up and down transpose. Hold Shift for one beat or one octave."
        )

    def pattern(self):
        return self.app.project.pattern()

    def visible_indices(self):
        return {i for i, note in enumerate(self.pattern().notes) if self.panel.matches(note)}

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
                if self.panel.matches(self.pattern().notes[i])
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
            self.cursor_beat = max(0, self.snap((pos.x() - KEY_W) / self.px_per_beat))
            self.update()
            return
        if pos.x() < self.panel.scroll.horizontalScrollBar().value() + KEY_W:
            if event.button() == Qt.LeftButton:
                self.app.play_selected_note(pitch, 0.8)
                self.drag = ("audition", pitch)
            return
        i = self.hit(pos)
        if event.button() == Qt.RightButton or (
            event.button() == Qt.LeftButton and self.tool == "erase"
        ):
            self.app.snapshot()
            self.drag = ("erase",)

            if i is not None:
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
        if i is None and self.tool == "select":
            self.drag = (
                "select",
                pos,
                set(self.selected) if event.modifiers() & Qt.ControlModifier else set(),
            )
            self.marquee = QRectF(pos, pos)
            return
        if self.tool == "paint":
            self.app.snapshot()
            self.drag = ("paint",)
            self.paint_note(pos)
            return
        if i is None:
            beat = max(0, self.snap((pos.x() - KEY_W) / self.px_per_beat))
            if beat >= self.pattern().length_beats:
                return
            self.app.snapshot()
            duration = min(self.panel.duration.value(), self.pattern().length_beats - beat)
            self.pattern().notes.append(
                Note(
                    pitch,
                    beat,
                    duration,
                    self.panel.velocity.value() / 100,
                    self.panel.target_pad,
                    instrument=self.panel.target_instrument,
                )
            )
            self.selected = {len(self.pattern().notes) - 1}
            self.cursor_beat = beat
            self.drag = (
                "resize",
                pos,
                {j: replace(self.pattern().notes[j]) for j in self.selected},
            )
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
        if self.drag[0] == "select":
            _, origin, previous = self.drag
            self.marquee = QRectF(origin, event.position()).normalized()
            self.selected = previous | {
                i
                for i in self.visible_indices()
                if self.rect_for(self.pattern().notes[i]).intersects(self.marquee)
            }
            self.selectionChanged.emit()
            self.update()
            return
        if self.drag[0] == "paint":
            self.paint_note(event.position())
            return
        if self.drag[0] == "erase":
            i = self.hit(event.position())
            if i is not None:
                del self.pattern().notes[i]
                self.selected.clear()
                self.commit()
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
        self.marquee = None
        self.update()

    def paint_note(self, pos):
        if pos.x() < self.panel.scroll.horizontalScrollBar().value() + KEY_W:
            return
        beat = max(0, self.snap((pos.x() - KEY_W) / self.px_per_beat))
        pitch = max(0, min(127, 127 - int((pos.y() - RULER_H) / ROW_H)))
        if beat >= self.pattern().length_beats or any(
            n.pitch == pitch and abs(n.start - beat) < 1e-8 and self.panel.matches(n)
            for n in self.pattern().notes
        ):
            return
        self.pattern().notes.append(
            Note(
                pitch,
                beat,
                min(self.panel.snap.currentData(), self.pattern().length_beats - beat),
                self.panel.velocity.value() / 100,
                self.panel.target_pad,
                instrument=self.panel.target_instrument,
            )
        )
        self.selected.add(len(self.pattern().notes) - 1)
        self.commit()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.panel.zoom_time(4 / 3 if event.angleDelta().y() > 0 else 0.75)
            event.accept()
            return
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

    def move_selected_in_time(self, delta):
        """Move the whole selected phrase without letting any note cross the pattern edge."""
        if not self.selected:
            return
        notes = [self.pattern().notes[i] for i in self.selected]
        length = self.pattern().length_beats
        applied = max(
            -min(note.start for note in notes),
            min(delta, min(length - note.start - note.duration for note in notes)),
        )
        if not applied:
            return
        self.app.snapshot()
        for note in notes:
            note.start += applied
        self.commit()

    def transpose_selected(self, delta):
        if not self.selected:
            return
        notes = [self.pattern().notes[i] for i in self.selected]
        applied = max(
            -min(note.pitch for note in notes), min(delta, 127 - max(note.pitch for note in notes))
        )
        if not applied:
            return
        self.app.snapshot()
        for note in notes:
            note.pitch += applied
        self.commit()

    def select_all(self):
        self.selected = self.visible_indices()
        self.selectionChanged.emit()
        self.update()

    def duplicate_selected(self):
        if not self.selected:
            return
        originals = [replace(self.pattern().notes[i]) for i in sorted(self.selected)]
        first = min(n.start for n in originals)
        end = max(n.start + n.duration for n in originals)
        step = self.panel.snap.currentData()
        shift = max(step, math.ceil((end - first) / step) * step)
        needed = math.ceil((end + shift) / 4)
        if needed > 256:
            self.app.status.showMessage("Pattern limit is 256 bars", 3000)
            return
        self.app.snapshot()
        self.pattern().bars = max(self.pattern().bars, needed)
        self.selected = set(
            range(len(self.pattern().notes), len(self.pattern().notes) + len(originals))
        )
        self.pattern().notes.extend(replace(n, start=n.start + shift) for n in originals)
        self.panel.sync_length()
        self.commit()

    def legato(self):
        indices = sorted(
            self.selected or self.visible_indices(), key=lambda i: self.pattern().notes[i].start
        )
        if not indices:
            return
        self.app.snapshot()
        for i in indices:
            note = self.pattern().notes[i]
            following = [
                self.pattern().notes[j].start
                for j in indices
                if self.pattern().notes[j].start > note.start
            ]
            note.duration = (
                min(following) if following else self.pattern().length_beats
            ) - note.start
        self.commit()

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride:
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            alt = bool(event.modifiers() & Qt.AltModifier)
            if (
                ctrl
                and event.key()
                in (Qt.Key_A, Qt.Key_C, Qt.Key_X, Qt.Key_V, Qt.Key_D, Qt.Key_Q, Qt.Key_L)
            ) or (alt and event.key() in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4)):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):
        key, ctrl = event.key(), bool(event.modifiers() & Qt.ControlModifier)
        if event.modifiers() & Qt.AltModifier and key in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            self.panel.set_tool(("select", "draw", "paint", "erase")[key - Qt.Key_1])
        elif ctrl and key == Qt.Key_D:
            self.duplicate_selected()
        elif ctrl and key == Qt.Key_L:
            self.legato()
        elif ctrl and key == Qt.Key_Q:
            self.panel.quantize()
        elif ctrl and key == Qt.Key_X:
            self.clipboard = [replace(self.pattern().notes[i]) for i in sorted(self.selected)]
            self.delete_selected()
        elif key == Qt.Key_Escape:
            self.selected.clear()
            self.selectionChanged.emit()
            self.update()
        elif key in (Qt.Key_Home, Qt.Key_End):
            self.cursor_beat = (
                0
                if key == Qt.Key_Home
                else max((n.start + n.duration for n in self.pattern().notes), default=0)
            )
            self.update()
        elif ctrl and key == Qt.Key_A:
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
            destination = max(0, self.snap(self.cursor_beat))
            end = destination + max(n.start + n.duration for n in self.clipboard) - origin
            self.pattern().bars = min(256, max(self.pattern().bars, math.ceil(end / 4)))
            self.panel.sync_length()
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
                            instrument=self.panel.target_instrument,
                            duration=min(n.duration, self.pattern().length_beats - start),
                        )
                    )
            self.commit()
        elif key in (Qt.Key_Left, Qt.Key_Right) and self.selected:
            delta = 1 if key == Qt.Key_Right else -1
            self.move_selected_in_time(
                delta
                * (1.0 if event.modifiers() & Qt.ShiftModifier else self.panel.snap.currentData())
            )
        elif key in (Qt.Key_Up, Qt.Key_Down) and self.selected:
            delta = (12 if event.modifiers() & Qt.ShiftModifier else 1) * (
                1 if key == Qt.Key_Up else -1
            )
            self.transpose_selected(delta)
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
        first_tick = max(0, int((event.rect().left() - KEY_W) / self.px_per_beat / step))
        last_tick = min(
            int(self.pattern().length_beats / step),
            int((event.rect().right() - KEY_W) / self.px_per_beat / step) + 1,
        )
        for tick in range(first_tick, last_tick + 1):
            beat = tick * step
            x = int(KEY_W + beat * self.px_per_beat)
            is_bar = abs(beat / 4 - round(beat / 4)) < 1e-6
            is_beat = abs(beat - round(beat)) < 1e-6
            painter.setPen(
                QPen(
                    q("cell_bar" if is_bar else "hover_line" if is_beat else "line"),
                    2 if is_bar else 1,
                )
            )
            painter.drawLine(x, event.rect().top(), x, event.rect().bottom())
        painter.setRenderHint(QPainter.Antialiasing)
        for i, note in enumerate(self.pattern().notes):
            if not self.panel.matches(note):
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
        if self.px_per_beat >= 32:
            for beat in range(int(self.pattern().length_beats)):
                if beat % 4:
                    painter.setPen(q("dim"))
                    painter.drawText(
                        int(KEY_W + beat * self.px_per_beat + 4),
                        top + 19,
                        f"{beat // 4 + 1}.{beat % 4 + 1}",
                    )
        # Keep the audition keyboard visible while navigating long patterns.
        left = self.panel.scroll.horizontalScrollBar().value()
        painter.save()
        painter.setClipRect(QRectF(left, top + RULER_H, KEY_W, self.height()), Qt.IntersectClip)
        for row in range(first, last):
            pitch, y = 127 - row, RULER_H + row * ROW_H
            painter.fillRect(
                left,
                y,
                KEY_W,
                ROW_H,
                q("bg3") if pitch % 12 in (1, 3, 6, 8, 10) else q("cell_beat"),
            )
            painter.setPen(q("fg"))
            painter.drawText(
                QRectF(left + 4, y, KEY_W - 9, ROW_H),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}",
            )
        painter.restore()
        if self.marquee is not None:
            painter.setPen(QPen(q("accent2"), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.marquee)
        painter.setPen(QPen(q("accent2"), 1, Qt.DashLine))
        cursor_x = int(KEY_W + self.cursor_beat * self.px_per_beat)
        painter.drawLine(cursor_x, top + RULER_H, cursor_x, event.rect().bottom())
        painter.end()


class PianoRollPanel(WindowClient, QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.target_pad = None
        self.target_instrument = None
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
        self.length_bars = QSpinBox()
        self.length_bars.setRange(1, 256)
        self.length_bars.setSuffix(" bars")
        self.length_bars.setKeyboardTracking(False)
        self.length_bars.setAccessibleName("Piano roll pattern length")
        self.length_bars.valueChanged.connect(self.set_length)
        sound.insertWidget(sound.count() - 1, self.length_bars)
        self.arrange_button = QPushButton("Add to Song")
        self.arrange_button.setToolTip("Place this pattern's notes and steps on the song timeline")
        self.arrange_button.clicked.connect(lambda: self.app.append_pattern_to_arrangement())
        tools.addWidget(self.arrange_button)
        self.snap = QComboBox()
        for label, beats in (
            ("1/4", 1.0),
            ("1/8", 0.5),
            ("1/16", 0.25),
            ("1/32", 0.125),
            ("1/64", 0.0625),
            ("1/8 triplet", 1 / 3),
            ("1/16 triplet", 1 / 6),
        ):
            self.snap.addItem(label, beats)
        self.snap.setCurrentIndex(2)
        tools.addWidget(QLabel("Snap"))
        tools.addWidget(self.snap)
        note_actions = QPushButton("Note tools")
        note_menu = QMenu(note_actions)
        note_menu.addAction("Quantize", self.quantize)
        note_menu.addAction("Select all", lambda: self.canvas.select_all())
        note_menu.addAction("Duplicate", lambda: self.canvas.duplicate_selected())
        note_menu.addAction("Legato", lambda: self.canvas.legato())
        pattern_menu = note_menu.addMenu("Pattern")
        pattern_menu.addAction("New note pattern", self.new_note_pattern)
        pattern_menu.addAction("Generate…", self.show_generator)
        pattern_menu.addAction("Extend ×2", self.extend_pattern)
        chord_menu = note_menu.addMenu("Add chord")
        note_actions.setMenu(note_menu)
        tools.addWidget(note_actions)
        self.chord = QComboBox(self)
        self.chord.hide()
        for title, intervals in (
            ("Major", (0, 4, 7)),
            ("Minor", (0, 3, 7)),
            ("Major 7", (0, 4, 7, 11)),
            ("Minor 7", (0, 3, 7, 10)),
        ):
            self.chord.addItem(title, intervals)
        for index in range(self.chord.count()):

            def insert_chord(checked=False, index=index):
                self.chord.setCurrentIndex(index)
                self.add_chord()

            chord_menu.addAction(self.chord.itemText(index), insert_chord)
        fit = QPushButton("Fit")
        fit.setAccessibleName("Fit pattern")
        fit.setToolTip("Fit the full pattern in the editor")
        fit.clicked.connect(self.fit_pattern)
        tools.addWidget(fit)
        for label, factor in (("−", 0.75), ("+", 4 / 3)):
            zoom = QPushButton(label)
            zoom.setAccessibleName("Zoom out notes" if factor < 1 else "Zoom in notes")
            zoom.clicked.connect(lambda checked=False, factor=factor: self.zoom_time(factor))
            tools.addWidget(zoom)
        focus = QPushButton("Center")
        focus.setAccessibleName("Find notes")
        focus.setToolTip("Center the keyboard on this sound’s notes")
        focus.clicked.connect(self.focus_sound)
        tools.addWidget(focus)
        self.tool_selector = QComboBox()
        self.tool_selector.setAccessibleName("Note editing tool")
        for tool in ("select", "draw", "paint", "erase"):
            self.tool_selector.addItem(tool.title(), tool)
        self.tool_selector.setCurrentIndex(1)
        self.tool_selector.setToolTip("Select, draw, paint or erase notes · Alt+1–4")
        self.tool_selector.currentIndexChanged.connect(
            lambda: self.set_tool(self.tool_selector.currentData())
        )
        tools.insertWidget(0, self.tool_selector)
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
        self.start = QDoubleSpinBox()
        self.start.setRange(0, 1024)
        self.start.setDecimals(5)
        self.start.setSingleStep(0.0625)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.03125, 1024)
        self.duration.setDecimals(5)
        self.duration.setSingleStep(0.25)
        self.duration.setValue(1)
        self.velocity = QSpinBox()
        self.velocity.setRange(1, 100)
        self.velocity.setValue(80)
        for name, control in (
            ("Start (beats)", self.start),
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
            "Alt+1–4 tools · Ctrl+D duplicate · Ctrl+Q quantize · Ctrl+L legato · "
            "Ctrl+X/C/V cut/copy/paste · Ctrl+wheel zoom · Arrows move · Shift: beat/octave"
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

    def sync_length(self):
        self.length_bars.blockSignals(True)
        self.length_bars.setValue(self.app.project.pattern().bars)
        self.length_bars.blockSignals(False)
        self.app.bars_box.blockSignals(True)
        text = str(self.app.project.pattern().bars)
        if self.app.bars_box.findText(text) < 0:
            self.app.bars_box.addItem(text)
        self.app.bars_box.setCurrentText(text)
        self.app.bars_box.blockSignals(False)
        self.app.step_grid.refresh()

    def set_length(self, bars):
        pattern = self.app.project.pattern()
        end = max(
            [
                0,
                *[n.start + n.duration for n in pattern.notes],
                *[(step + 1) / pattern.div for row in pattern.steps.values() for step in row],
            ]
        )
        if end > bars * 4:
            self.app.status.showMessage(
                "Existing notes or steps extend past that length. Move or trim them first.", 5000
            )
            self.sync_length()
            return
        if bars != pattern.bars:
            self.app.snapshot()
            pattern.bars = bars
            self.sync_length()
            self.canvas.refresh()
            self.app.playlist.refresh()

    def extend_pattern(self):
        self.set_length(min(256, self.app.project.pattern().bars * 2))

    def new_note_pattern(self):
        from ..model import Pattern

        self.app.snapshot()
        pattern = Pattern(
            name=f"Notes {len(self.app.project.patterns) + 1}", bars=self.length_bars.value()
        )
        self.app.project.patterns.append(pattern)
        self.app.project.current_pattern = pattern.id
        self.app._sync_pattern_controls()
        self.app._refresh_place_box()

    def generate_pattern(self, bars, root, scale, style, step, seed):
        from ..note_generator import generate_notes
        from ..model import Pattern

        notes = generate_notes(
            bars, root, scale, style, step, seed, self.target_pad, self.target_instrument
        )
        self.app.snapshot()
        pattern = Pattern(
            name=f"{scale} {style} {len(self.app.project.patterns) + 1}", bars=bars, notes=notes
        )
        self.app.project.patterns.append(pattern)
        self.app.project.current_pattern = pattern.id
        self.app._sync_pattern_controls()
        self.app._refresh_place_box()
        self.fit_pattern()
        self.focus_sound()

    def show_generator(self):
        from ..note_generator import SCALES

        dialog = QDialog(self)
        dialog.setWindowTitle("Generate an editable note pattern")
        form = QFormLayout(dialog)
        bars = QSpinBox()
        bars.setRange(1, 256)
        bars.setValue(self.length_bars.value())
        root = QSpinBox()
        root.setRange(0, 127)
        root.setValue(self.pitch.value())
        scale = QComboBox()
        scale.addItems(SCALES)
        style = QComboBox()
        style.addItems(["Chords", "Arpeggio", "Bass", "Melody"])
        density = QComboBox()
        for name, value in (
            ("Quarter notes", 1.0),
            ("Eighth notes", 0.5),
            ("Sixteenth notes", 0.25),
        ):
            density.addItem(name, value)
        density.setCurrentIndex(1)
        seed = QSpinBox()
        seed.setRange(0, 999999)
        for label, widget in (
            ("Length", bars),
            ("Root MIDI", root),
            ("Scale", scale),
            ("Phrase", style),
            ("Rhythm", density),
            ("Variation", seed),
        ):
            form.addRow(label, widget)
        form.addRow(QLabel("Creates a new pattern. Your existing notes stay intact."))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Generate new pattern")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.generate_pattern(
                bars.value(),
                root.value(),
                scale.currentText(),
                style.currentText(),
                density.currentData(),
                seed.value(),
            )

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
        self.sync_length()
        self.sync_channels()
        self.canvas.refresh()

    def sync_channels(self):
        project = self.app.project
        referenced = {n.pad for p in project.patterns for n in p.notes if n.pad is not None}
        self.channel.blockSignals(True)
        self.channel.clear()
        plugin = project.plugins.get("instrument", {})
        external = getattr(getattr(self.app.engine, "external", None), "instrument", None)
        instrument_name = (
            f"VST · {Path(plugin.get('path', '')).stem or 'External instrument'}"
            if external is not None
            else f"Synth · {project.synth.name}"
        )
        self.channel.addItem(instrument_name, None)
        for instrument in project.instruments:
            bridge = self.app.engine.external_for(instrument.id).instrument
            label = (
                f"{instrument.name} · VST · "
                f"{Path((instrument.plugin or {}).get('path', '')).stem or 'External instrument'}"
                if bridge is not None
                else f"{instrument.name} · {instrument.patch.name}"
            )
            self.channel.addItem(label, instrument.id)
        for index, pad in enumerate(project.pads):
            if not pad.empty or index in referenced:
                label = f"{chr(65 + index // 16)}{index % 16 + 1} · {pad.name or 'Missing sound'}"
                self.channel.addItem(label, index)
        selected = self.channel.findData(
            self.target_instrument if self.target_instrument is not None else self.target_pad
        )
        if selected < 0:
            selected, self.target_pad = 0, None
            self.target_instrument = None
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
        if isinstance(index, str):
            self.app.project.instrument_patch(index)
            self.target_pad, self.target_instrument = None, index
        else:
            self.target_pad, self.target_instrument = index, None
        if self.target_pad is None:
            selected = self.app.project.selected_instrument
            if selected != self.target_instrument:
                self.app.panic_synth()
                self.app.project.selected_instrument = self.target_instrument
                self.app.synth_panel.sync()
        if hasattr(self.app.engine, "midi"):
            self.app.engine.midi.route = (
                self.target_pad,
                self.target_instrument,
                self.app.pads.bank,
            )
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
        pitches = [n.pitch for n in self.canvas.pattern().notes if self.matches(n)]
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

    def set_tool(self, tool):
        self.canvas.tool = tool
        self.tool_selector.blockSignals(True)
        self.tool_selector.setCurrentIndex(self.tool_selector.findData(tool))
        self.tool_selector.blockSignals(False)
        self.canvas.setFocus()

    def zoom(self, factor):
        self.zoom_time(factor)

    def zoom_time(self, factor):
        bar = self.scroll.horizontalScrollBar()
        centre = (
            bar.value() + self.scroll.viewport().width() / 2 - KEY_W
        ) / self.canvas.px_per_beat
        self.canvas.px_per_beat = min(512, max(12, self.canvas.px_per_beat * factor))
        self.canvas.refresh()
        bar.setValue(
            round(KEY_W + centre * self.canvas.px_per_beat - self.scroll.viewport().width() / 2)
        )

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
            (self.start, note.start),
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
            if sender == self.start:
                length = self.canvas.pattern().length_beats
                note.duration = min(note.duration, length)
                note.start = max(0, min(self.start.value(), length - note.duration))
            elif sender == self.pitch:
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
                        instrument=self.target_instrument,
                    )
                )
        self.canvas.commit()

    def matches(self, note):
        return note.pad == self.target_pad and note.instrument == self.target_instrument
