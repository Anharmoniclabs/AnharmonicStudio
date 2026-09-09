"""Pattern editing and placement actions shared by the workstation views."""

from __future__ import annotations

from dataclasses import replace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog

from ..model import Pattern, Clip, Row, uid
from ..workflow import pattern_arrangement_target
from .playlist import ROW_H, RULER_H


class PatternActionsMixin:
    def _sync_pattern_controls(self):
        self.pattern_box.blockSignals(True)
        self.pattern_box.clear()
        for pat in self.project.patterns:
            self.pattern_box.addItem(pat.name, pat.id)
        idx = next(
            (
                i
                for i, p in enumerate(self.project.patterns)
                if p.id == self.project.current_pattern
            ),
            0,
        )
        self.pattern_box.setCurrentIndex(idx)
        self.pattern_box.blockSignals(False)

        pat = self.project.pattern()
        self.bars_box.blockSignals(True)
        self.bars_box.setCurrentText(str(pat.bars))
        self.bars_box.blockSignals(False)
        self.grid_box.blockSignals(True)
        self.grid_box.setCurrentIndex(max(0, self.grid_box.findData(pat.div)))
        self.grid_box.blockSignals(False)
        self.step_grid.refresh()
        if hasattr(self, "piano_roll"):
            self.piano_roll.sync()

    def _pattern_picked(self, idx):
        if idx < 0:
            return
        self.project.current_pattern = self.pattern_box.itemData(idx)
        self._sync_pattern_controls()
        self._refresh_place_box()

    def new_pattern(self):
        self.snapshot()
        pat = Pattern(name=f"pattern {len(self.project.patterns) + 1}")
        self.project.patterns.append(pat)
        self.project.current_pattern = pat.id
        self._sync_pattern_controls()
        self._refresh_place_box()

    def dup_pattern(self):
        self.snapshot()
        src = self.project.pattern()
        copy = Pattern(
            id=uid(),
            name=f"{src.name} copy",
            bars=src.bars,
            div=src.div,
            steps={k: dict(v) for k, v in src.steps.items()},
            notes=[replace(note) for note in src.notes],
        )
        self.project.patterns.append(copy)
        self.project.current_pattern = copy.id
        self._sync_pattern_controls()
        self._refresh_place_box()

    def rename_pattern(self):
        pat = self.project.pattern()
        name, ok = QInputDialog.getText(self, "Rename pattern", "Name:", text=pat.name)
        if ok and name:
            self.snapshot()
            pat.name = name
            self._sync_pattern_controls()
            self._refresh_place_box()

    def clear_pattern(self):
        self.snapshot()
        self.project.pattern().steps.clear()
        self.project.pattern().notes.clear()
        self._sync_pattern_controls()

    def step_grid_only_loaded(self, on: bool):
        self.step_grid.set_only_loaded(on)
        hidden = 16 - len(self.step_grid.lanes())
        self.status.showMessage(
            f"{hidden} empty lane{'' if hidden == 1 else 's'} hidden"
            if hidden
            else "showing every lane",
            2000,
        )

    def _follow_step(self, x: int, width: int):
        """Keep the playing column visible without fighting a manual scroll."""
        bar = self.seq_scroll.horizontalScrollBar()
        left, span = bar.value(), self.seq_scroll.viewport().width()
        if x < left + 140:
            bar.setValue(max(0, x - 140))
        elif x + width > left + span - 40:
            bar.setValue(int(x + width - span + 40))

    def double_pattern(self):
        """Twice the bars, with the existing steps repeated into the new half."""
        pat = self.project.pattern()
        if pat.bars >= 8:
            self.status.showMessage("8 bars is the longest pattern", 2000)
            return
        self.snapshot()
        span = pat.total_steps
        note_span = pat.length_beats
        pat.notes += [
            replace(n, start=n.start + note_span) for n in pat.notes if n.start < note_span
        ]
        pat.bars *= 2
        for gi, row in list(pat.steps.items()):
            pat.steps[gi] = {**row, **{step + span: vel for step, vel in row.items()}}
        self._sync_pattern_controls()
        self._set_dirty(True)
        self.status.showMessage(f"pattern doubled to {pat.bars} bars", 2500)

    def _bars_changed(self, value):
        self.snapshot()
        self.project.pattern().bars = int(value)
        length = self.project.pattern().length_beats
        self.project.pattern().notes = [
            replace(n, duration=min(n.duration, length - n.start))
            for n in self.project.pattern().notes
            if n.start < length
        ]
        self._sync_pattern_controls()

    def _div_changed(self, idx):
        self.snapshot()
        self.project.pattern().div = self.grid_box.itemData(idx)
        self.step_grid.refresh()

    # ── playlist ─────────────────────────────────────────────
    def append_pattern_to_arrangement(self) -> Clip | None:
        """Place the editable pattern after its destination lane's last clip."""
        pattern = self.project.pattern()
        if not any(pattern.steps.values()) and not pattern.notes:
            self.status.showMessage(
                "This pattern is empty. Add some steps or notes, then add it to the arrangement.",
                5000,
            )
            return None
        row_index, start = pattern_arrangement_target(
            self.project, pattern, self.playlist.selected_clip, self.playlist.snap
        )
        self.snapshot()
        if row_index == len(self.project.rows):
            self.project.rows.append(Row(name=f"TRACK {row_index + 1}"))
        clip = self.playlist._place_ref(row_index, start, "pattern", pattern.id)
        self._choose_pattern_to_place(pattern.id)
        self.playlist.select_clip(clip)
        if self.studio.enabled:
            self.studio.select(self.TAB_PLAYLIST)
        else:
            self.show_tab(self.TAB_PLAYLIST)
        self.playlist.setFocus(Qt.OtherFocusReason)
        self.song_scroll.ensureVisible(
            int(self.playlist.beat_to_x(start) + 20),
            int(RULER_H + (row_index + 0.5) * ROW_H),
            40,
            ROW_H,
        )
        self.status.showMessage(
            f"{pattern.name} added to {self.project.rows[row_index].name} "
            f"at beat {start + 1:g} · double-click the clip to edit · Ctrl+Z to undo",
            6000,
        )
        return clip

    def open_pattern_clip(self, clip: Clip) -> bool:
        """Navigate from a placed pattern to its original editable sequence."""
        pattern = next(
            (pattern for pattern in self.project.patterns if pattern.id == clip.ref), None
        )
        if clip.kind != "pattern" or pattern is None:
            self.status.showMessage("The pattern for this clip could not be found.", 4000)
            return False
        self.project.current_pattern = pattern.id
        self._sync_pattern_controls()
        self._refresh_place_box()
        self._choose_pattern_to_place(pattern.id)
        index = self.TAB_PIANO if pattern.notes else self.TAB_SEQ
        if pattern.notes:
            self.piano_roll.select_channel(pattern.notes[0].pad)
        if self.studio.enabled:
            self.studio.select(index)
        else:
            self.show_tab(index)
        editor = self.piano_roll.canvas if pattern.notes else self.step_grid
        editor.setFocus(Qt.OtherFocusReason)
        self.status.showMessage(
            f"Editing {pattern.name} · changes apply to every clip using this pattern", 5000
        )
        return True

    def make_pattern_unique(self, clip: Clip | None = None) -> Pattern | None:
        """Detach one arrangement placement's notes and steps from its source pattern."""
        clip = clip or self.playlist.selected_clip
        if clip is None or clip.kind != "pattern" or self.playlist.row_for_clip(clip) is None:
            return None
        source = next((p for p in self.project.patterns if p.id == clip.ref), None)
        if source is None:
            self.status.showMessage("The pattern for this clip could not be found.", 4000)
            return None
        self.snapshot()
        pattern = replace(
            source,
            id=uid(),
            name=f"{source.name} variation",
            steps={index: dict(steps) for index, steps in source.steps.items()},
            notes=[replace(note) for note in source.notes],
        )
        self.project.patterns.append(pattern)
        clip.ref = pattern.id
        self.project.current_pattern = pattern.id
        self._sync_pattern_controls()
        self._refresh_place_box()
        self._choose_pattern_to_place(pattern.id)
        self.playlist.select_clip(clip)
        self.playlist.refresh()
        self.status.showMessage(
            f"{pattern.name} has independent notes and steps · sounds remain shared · Ctrl+Z to undo",
            6000,
        )
        return pattern

    def _choose_pattern_to_place(self, pattern_id: str):
        # QVariant lookup does not reliably compare Python tuple item data.
        for index in range(self.place_box.count()):
            if self.place_box.itemData(index) == ("pattern", pattern_id):
                self.place_box.setCurrentIndex(index)
                break

    def _refresh_place_box(self):
        current = self.playlist.place or self.place_box.currentData()
        sample_selected = bool(
            current and current[0] == "audio" and current[1] in self.library.clips
        )
        self.place_box.blockSignals(True)
        self.place_box.clear()
        for pat in self.project.patterns:
            self.place_box.addItem(f"▦  {pat.name}", ("pattern", pat.id))
        if sample_selected:
            self.place_box.setCurrentIndex(-1)
        elif current:
            for idx in range(self.place_box.count()):
                if self.place_box.itemData(idx) == current:
                    self.place_box.setCurrentIndex(idx)
                    break
        self.place_box.blockSignals(False)
        self.playlist.place = current if sample_selected else self.place_box.currentData()
        self.playlist.refresh()

    def _place_changed(self, idx):
        self.playlist.place = self.place_box.itemData(idx)
        self.playlist.place_template = None

    def _zoom_changed(self, v):
        self.playlist.px_per_beat = float(v)
        self.playlist.refresh()
