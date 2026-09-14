"""Persistent note regions over the existing waveform and pitch view."""

from __future__ import annotations
from copy import deepcopy

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QInputDialog, QMenu

from . import theme
from .vocal_pitch import VocalPitchView
from ..autotune.targeting import notes_from_analysis, correction
import numpy as np


class VocalNoteEditor(VocalPitchView):
    editsChanged = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_revision = 0
        self._notes_cache = None
        self._guide_dirty = True
        self.selected = set()
        self._note_drag = None
        self.setToolTip(
            "V2: drag notes to change pitch; Ctrl-click selects multiple. S splits at cursor, "
            "J joins adjacent notes, B bypasses, Delete resets edits. Waveform drag selects audio."
        )

    def notes(self):
        if not self.clip_id or self.analysis is None:
            return []
        if self._notes_cache is None:
            saved = self.settings.pitch_edits.get(self.clip_id)
            self._notes_cache = (
                deepcopy(saved)
                if saved is not None
                else notes_from_analysis(self.analysis, self.duration, self.settings)
            )
        return deepcopy(self._notes_cache)

    def set_settings(self, settings):
        self._edit_revision += 1
        self._note_drag = None
        self._notes_cache = None
        self._guide_dirty = True
        super().set_settings(settings)

    def _finished(self, generation, analysis, error):
        self._notes_cache = None
        self._guide_dirty = True
        super()._finished(generation, analysis, error)

    def set_source(self, clip_id, audio, sr):
        if clip_id != self.clip_id or audio is not self.audio:
            self._edit_revision += 1
            self._notes_cache = None
            self._guide_dirty = True
            self._note_drag = None
        if clip_id != self.clip_id:
            self.selected.clear()
            self._note_drag = None
            self._notes_cache = None
            self._guide_dirty = True
        super().set_source(clip_id, audio, sr)

    def _commit(self, notes):
        if self.clip_id:
            self.editsChanged.emit(self.clip_id, notes)
            self.update()

    def _rect(self, note):
        return QRectF(
            self.x_at(note["start"]),
            self.pitch_y_at(note["target"]) - 7,
            max(3, self.x_at(note["end"]) - self.x_at(note["start"])),
            14,
        )

    def paintEvent(self, event):
        if self._guide_dirty and self.settings.backend == "v2" and self.analysis is not None:
            ratios = correction(self.analysis, self.settings, self.notes())
            self.guide = self.analysis.detected_midi + 12 * np.log2(ratios) * self.settings.mix
            self._guide_dirty = False
        super().paintEvent(event)
        if self.settings.backend != "v2":
            return
        p = QPainter(self)
        p.setClipRect(
            QRectF(48, self.PITCH_TOP, self.width() - 70, self.height() - 30 - self.PITCH_TOP)
        )
        for i, note in enumerate(self.notes()):
            p.setPen(
                QPen(
                    theme.q("fg" if i in self.selected else "accent"),
                    2 if i in self.selected else 1,
                )
            )
            p.setBrush(theme.q("dim" if note.get("bypass") else "accent", 65))
            p.drawRoundedRect(self._rect(note), 3, 3)
        p.end()

    def mousePressEvent(self, event):
        if (
            self.settings.backend == "v2"
            and event.button() == Qt.LeftButton
            and event.position().y() >= self.PITCH_TOP
        ):
            self.setFocus()
            self.cursor = self.time_at(event.position().x())
            notes = self.notes()
            hit = next(
                (i for i, n in enumerate(notes) if self._rect(n).contains(event.position())), None
            )
            if hit is not None:
                if event.modifiers() & Qt.ControlModifier:
                    self.selected.symmetric_difference_update({hit})
                elif hit not in self.selected:
                    self.selected = {hit}
                self._note_drag = (event.position().y(), notes)
                self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._note_drag:
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._note_drag:
            y, notes = self._note_drag
            self._note_drag = None
            delta = round((y - event.position().y()) / self.note_height)
            if delta:
                chosen = [notes[i] for i in self.selected if i < len(notes)]
                if chosen:
                    delta = max(
                        -min(n["target"] for n in chosen),
                        min(delta, 127 - max(n["target"] for n in chosen)),
                    )
                    for note in chosen:
                        note["target"] += delta
                    self._commit(notes)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.settings.backend != "v2" or event.modifiers() & (
            Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier | Qt.ShiftModifier
        ):
            return super().keyPressEvent(event)
        notes = self.notes()
        selected = sorted(i for i in self.selected if i < len(notes))
        key = event.key()
        if key == Qt.Key_Delete:
            self.selected.clear()
            self._commit(None)
        elif key == Qt.Key_B and selected:
            bypass = not all(notes[i].get("bypass", False) for i in selected)
            for i in selected:
                notes[i]["bypass"] = bypass
            self._commit(notes)
        elif key == Qt.Key_S and len(selected) == 1:
            i = selected[0]
            note = notes[i]
            split = (
                self.cursor
                if note["start"] + 0.01 < self.cursor < note["end"] - 0.01
                else (note["start"] + note["end"]) / 2
            )
            notes[i : i + 1] = [dict(note, end=split), dict(note, start=split)]
            self._commit(notes)
        elif (
            key == Qt.Key_J
            and len(selected) > 1
            and selected == list(range(selected[0], selected[-1] + 1))
        ):
            lo, hi = selected[0], selected[-1]
            notes[lo : hi + 1] = [dict(notes[lo], end=notes[hi]["end"])]
            self.selected = {lo}
            self._commit(notes)
        elif key in (Qt.Key_Up, Qt.Key_Down) and selected:
            delta = 1 if key == Qt.Key_Up else -1
            if all(0 <= notes[i]["target"] + delta <= 127 for i in selected):
                for i in selected:
                    notes[i]["target"] += delta
                self._commit(notes)
        else:
            return super().keyPressEvent(event)
        event.accept()

    def contextMenuEvent(self, event):
        if self.settings.backend != "v2" or not self.selected:
            return super().contextMenuEvent(event)
        revision = self._edit_revision
        menu = QMenu(self)
        strength = menu.addAction("Selected notes: correction strength…")
        if menu.exec(event.globalPos()) is not strength or revision != self._edit_revision:
            return
        notes = self.notes()
        selected = [i for i in self.selected if i < len(notes)]
        if not selected:
            return
        value, accepted = QInputDialog.getDouble(
            self,
            "Note correction",
            "Correction strength (%)",
            notes[selected[0]].get("strength", 1.0) * 100,
            0,
            100,
            1,
        )
        if accepted and revision == self._edit_revision:
            for i in selected:
                notes[i]["strength"] = value / 100
            self._commit(notes)
