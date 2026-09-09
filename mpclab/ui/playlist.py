"""Playlist / arrangement timeline — pattern blocks and audio clips on lanes."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import math

from .window_client import WindowClient

from PySide6.QtCore import Qt, QRectF, QSize, QPointF, Signal, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics, QIcon, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget, QMenu

from ..library import AUDIO_EXT
from ..model import Clip, uid
from .theme import q, TRACK_COLORS, is_light
from .waveform import draw_peaks
from .sample_drag import RANGE_MIME, sample_range

HEAD_W = 190
ROW_H = 54
RULER_H = 22


# Tool shortcuts: FL Studio's letters, and this app's original numbers.  The
# main window handles these too, for when the Playlist does not hold focus —
# both read this one table.
TOOL_KEYS = {
    Qt.Key_E: "select",
    Qt.Key_P: "draw",
    Qt.Key_B: "paint",
    Qt.Key_C: "slice",
    Qt.Key_T: "mute",
    Qt.Key_D: "erase",
    Qt.Key_1: "select",
    Qt.Key_2: "draw",
    Qt.Key_3: "paint",
    Qt.Key_4: "slice",
    Qt.Key_5: "mute",
    Qt.Key_6: "erase",
}


class PlaylistView(WindowClient, QWidget):
    changed = Signal()
    seek = Signal(float)
    selectionChanged = Signal(object)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.px_per_beat = 26.0
        self.snap = 4.0
        self.place = None  # ("pattern"|"audio", ref)
        self.place_template: Clip | None = None
        self.clipboard = []
        self.paste_row = 0
        self.tool = "draw"
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self._drag = None
        self._ruler_drag = None
        self._ruler_seek = None
        self._hover_clip = None
        self.selected_clip: Clip | None = None
        self.selected_clips: list[Clip] = []
        self._marquee = None
        self._marquee_selection = []
        self._paint_active = False
        self._paint_last = None
        self._paint_snapshot = False
        self._row_drag = None
        self._row_snapshot = False
        self._drop_target = None  # (row index, beat)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self._last_beat = -1.0
        self.setFocusPolicy(Qt.StrongFocus)

    def update_cursor(self):
        cursors = {
            "select": Qt.ArrowCursor,
            "draw": Qt.CrossCursor,
            "paint": Qt.CrossCursor,
            "slice": Qt.SplitHCursor,
            "mute": Qt.PointingHandCursor,
            "erase": Qt.ForbiddenCursor,
        }
        self.setCursor(cursors.get(self.tool, Qt.ArrowCursor))

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.tool in ("select", "draw", "paint"):
            hit = self.clip_at(ev.position().x(), ev.position().y())
            if hit:
                self._drag = None
                self._paint_active = False
                self.select_clip(hit[2])
                if hit[2].kind == "pattern":
                    self.app.open_pattern_clip(hit[2])
                else:
                    self.app.sample_workflow.from_arrangement(hit[2])
                ev.accept()
                return
        if ev.position().x() < HEAD_W:
            ri = self.row_at(ev.position().y())
            if ri >= 0:
                self.app.rename_song_row(self.rows()[ri])
                return
        super().mouseDoubleClickEvent(ev)

    # ── geometry ─────────────────────────────────────────────
    def rows(self):
        return self.app.project.rows

    def minimumSizeHint(self) -> QSize:
        length = max(self.app.project.song_end() + 32, self.app.project.loop_end + 8, 64)
        return QSize(
            int(HEAD_W + length * self.px_per_beat + 40),
            int(RULER_H + len(self.rows()) * ROW_H + 20),
        )

    def refresh(self):
        self.updateGeometry()
        self.resize(self.minimumSizeHint())
        self.update()

    def beat_to_x(self, b: float) -> float:
        return HEAD_W + b * self.px_per_beat

    def x_to_beat(self, x: float) -> float:
        return max(0.0, (x - HEAD_W) / self.px_per_beat)

    def row_at(self, y: float) -> int:
        if y < RULER_H:
            return -1
        i = int((y - RULER_H) // ROW_H)
        return i if 0 <= i < len(self.rows()) else -1

    def _snap(self, beat: float) -> float:
        if self.snap <= 0:
            return max(0.0, beat)
        return max(0.0, round(beat / self.snap) * self.snap)

    def clip_at(self, x: float, y: float):
        ri = self.row_at(y)
        if ri < 0 or x < HEAD_W:
            return None
        beat = self.x_to_beat(x)
        row = self.rows()[ri]
        for clip in reversed(row.clips):
            if clip.start_beat <= beat <= clip.start_beat + clip.length_beats:
                left = abs(self.beat_to_x(clip.start_beat) - x) < 7
                right = abs(self.beat_to_x(clip.start_beat + clip.length_beats) - x) < 7
                edge = "left" if left else ("right" if right else None)
                return ri, row, clip, edge
        return None

    # ── interaction ──────────────────────────────────────────
    def mousePressEvent(self, ev):
        self.setFocus()
        pos = ev.position()
        if pos.y() < RULER_H:
            if ev.button() == Qt.LeftButton:
                raw_beat = self.x_to_beat(pos.x())
                self._ruler_seek = raw_beat
                beat = self._snap(raw_beat)
                self._ruler_drag = (beat, beat)
            return

        if pos.x() < HEAD_W:
            ri = self.row_at(pos.y())
            if ri >= 0:
                self.paste_row = ri
                row = self.rows()[ri]
                if hasattr(self.app, "track_inspector"):
                    self.app.track_inspector.select_row(row)
                if (
                    ev.button() == Qt.LeftButton
                    and HEAD_W - 66 <= pos.x() < HEAD_W - 44
                    and hasattr(self.app, "track_capture")
                ):
                    self.app.track_capture.arm(row)
                    return
                if ev.button() == Qt.RightButton:
                    self._row_menu(ev.globalPosition().toPoint(), row)
                    return
                if pos.x() >= HEAD_W - 22:
                    self.app.snapshot()
                    row.solo = not row.solo
                elif pos.x() >= HEAD_W - 44 or self.tool == "mute":
                    self.app.snapshot()
                    row.mute = not row.mute
                else:
                    self._row_drag = ri
                    self._row_snapshot = False
                    return
                self.changed.emit()
                self.update()
            return

        hit = self.clip_at(pos.x(), pos.y())
        if self.row_at(pos.y()) >= 0:
            self.paste_row = self.row_at(pos.y())
        if ev.button() == Qt.RightButton:
            if hit:
                _, _, clip, _ = hit
                self.select_clip(clip)
                self._clip_menu(ev.globalPosition().toPoint(), clip, self.x_to_beat(pos.x()))
            elif self.row_at(pos.y()) >= 0:
                menu = QMenu(self)
                paste = menu.addAction(
                    "Paste here · Ctrl+V",
                    lambda: self.paste_clips(self.x_to_beat(pos.x()), self.row_at(pos.y())),
                )
                paste.setEnabled(bool(self.clipboard))
                menu.exec(ev.globalPosition().toPoint())
            return
        if ev.button() != Qt.LeftButton:
            return

        if hit and self.tool == "slice":
            self.select_clip(hit[2])
            self.split_clip(hit[2], self.x_to_beat(pos.x()))
            return
        if hit and self.tool == "erase":
            self.select_clip(hit[2])
            self.delete_selected()
            return
        if hit and self.tool == "mute":
            self.app.snapshot()
            hit[2].mute = not hit[2].mute
            self.select_clip(hit[2])
            self.changed.emit()
            self.update()
            return

        if hit:
            ri, row, clip, edge = hit
            additive = bool(ev.modifiers() & Qt.ControlModifier)
            if additive or clip not in self.selected_clips or edge:
                self.select_clip(clip, additive=additive)
            else:
                self.selected_clip = clip
                self.selectionChanged.emit(clip)
            if additive:
                return
            copied = bool(ev.modifiers() & Qt.AltModifier)
            if copied:
                self.app.snapshot()
                clip = self.duplicate_clip(clip, notify=False)
                row = next(r for r in self.rows() if clip in r.clips)
                self.select_clip(clip)
            self._drag = {
                "snapshot": copied,
                "mode": (
                    "trim-left"
                    if edge == "left" and clip.kind == "audio"
                    else "resize"
                    if edge
                    else "move"
                ),
                "row_index": ri,
                "row": row,
                "clip": clip,
                "grab": self.x_to_beat(pos.x()) - clip.start_beat,
                "start": clip.start_beat,
                "length": clip.length_beats,
                "offset": clip.offset,
                "source_length": clip.source_length,
                "starts": {c.id: c.start_beat for c in self.selected_clips},
                "rows": {
                    c.id: self.rows().index(self.row_for_clip(c)) for c in self.selected_clips
                },
            }
            return

        row_index = self.row_at(pos.y())
        start = self._snap(self.x_to_beat(pos.x()))
        if self.tool == "select":
            if not (ev.modifiers() & Qt.ControlModifier):
                self.select_clip(None)
            self._marquee_selection = list(self.selected_clips)
            self._marquee = (QPointF(pos), QPointF(pos))
            return
        if self.tool == "paint":
            self.select_clip(None)
            self._paint_active = True
            self._paint_last = None
            self._paint_snapshot = False
            self._paint_at(row_index, start)
            return
        if self.tool in ("slice", "mute", "erase"):
            return

        self.select_clip(None)
        placed = self._place_clip(row_index, start)
        if placed:
            self.select_clip(placed)
            row = self.rows()[row_index]
            self._drag = {
                "mode": "draw",
                "row_index": row_index,
                "row": row,
                "clip": placed,
                "grab": 0.0,
                "start": start,
                "length": placed.length_beats,
                "offset": placed.offset,
                "source_length": placed.source_length,
                "starts": {placed.id: placed.start_beat},
            }

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._row_drag is not None:
            ri = self.row_at(pos.y())
            if ri >= 0 and ri != self._row_drag:
                if not self._row_snapshot:
                    self.app.snapshot()
                    self._row_snapshot = True
                row = self.rows().pop(self._row_drag)
                self.rows().insert(ri, row)
                self._row_drag = ri
                self.update()
            return
        if self._marquee is not None:
            self._marquee = (self._marquee[0], QPointF(pos))
            self.update()
            return
        if self._paint_active:
            self._paint_at(self.row_at(pos.y()), self._snap(self.x_to_beat(pos.x())))
            return
        if self._ruler_drag is not None:
            self._ruler_drag = (self._ruler_drag[0], self._snap(self.x_to_beat(pos.x())))
            self.update()
            return
        if self._drag:
            beat = self.x_to_beat(pos.x())
            clip = self._drag["clip"]
            if self._drag["mode"] == "move":
                new_start = self._snap(beat - self._drag["grab"])
                delta = max(new_start - self._drag["start"], -min(self._drag["starts"].values()))
                ri = self.row_at(pos.y())
                primary_origin = self._drag["rows"][clip.id]
                origins = self._drag["rows"].values()
                row_delta = self._drag["row_index"] - primary_origin
                if ri >= 0:
                    row_delta = max(
                        -min(origins), min(len(self.rows()) - 1 - max(origins), ri - primary_origin)
                    )
                if not self._drag.get("snapshot", True) and (abs(delta) > 1e-9 or row_delta != 0):
                    self.app.snapshot()
                    self._drag["snapshot"] = True
                for selected in self.selected_clips:
                    origin = self._drag["starts"].get(selected.id)
                    if origin is not None:
                        selected.start_beat = max(0.0, origin + delta)
                if primary_origin + row_delta != self._drag["row_index"]:
                    for selected in self.selected_clips:
                        origin = self._drag["rows"].get(selected.id)
                        if origin is None:
                            continue
                        target_index = origin + row_delta
                        current = self.row_for_clip(selected)
                        target = self.rows()[target_index]
                        if current is not target:
                            current.clips.remove(selected)
                            target.clips.append(selected)
                    self._drag["row"] = self.row_for_clip(clip)
                    self._drag["row_index"] = primary_origin + row_delta
            elif self._drag["mode"] in ("resize", "draw"):
                minimum = self.snap or 0.25
                length = max(minimum, self._snap(beat - clip.start_beat) or minimum)
                if length != clip.length_beats and not self._drag.get("snapshot", True):
                    self.app.snapshot()
                    self._drag["snapshot"] = True
                clip.length_beats = length
            else:
                end = self._drag["start"] + self._drag["length"]
                new_start = min(end - (self.snap or 0.25), self._snap(beat))
                delta = new_start - self._drag["start"]
                spb = 60.0 / self.app.project.bpm
                max_back = self._drag["offset"] / spb
                delta = max(delta, -max_back)
                if abs(delta) > 1e-9 and not self._drag.get("snapshot", True):
                    self.app.snapshot()
                    self._drag["snapshot"] = True
                clip.start_beat = self._drag["start"] + delta
                clip.length_beats = end - clip.start_beat
                clip.offset = max(0.0, self._drag["offset"] + delta * spb)
                if self._drag["source_length"] > 0:
                    clip.source_length = max(0.001, self._drag["source_length"] - delta * spb)
            self.update()
            return

        hit = self.clip_at(pos.x(), pos.y())
        self._hover_clip = hit[2] if hit else None
        if pos.y() < RULER_H:
            self.setCursor(Qt.SplitHCursor)
        elif hit and self.tool in ("select", "draw"):
            self.setCursor(Qt.SizeHorCursor if hit[3] else Qt.OpenHandCursor)
        else:
            self.update_cursor()
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._row_drag is not None:
            self._row_drag = None
            if self._row_snapshot:
                self.changed.emit()
            self.refresh()
            return
        if self._paint_active:
            self._paint_active = False
            self._paint_last = None
            if self._paint_snapshot:
                self.changed.emit()
            self.refresh()
            return
        if self._marquee is not None:
            a, b = self._marquee
            self._marquee = None
            rect = QRectF(a, b).normalized()
            selected = list(self._marquee_selection)
            self._marquee_selection = []
            for ri, row in enumerate(self.rows()):
                y = RULER_H + ri * ROW_H
                for clip in row.clips:
                    cr = QRectF(
                        self.beat_to_x(clip.start_beat),
                        y,
                        clip.length_beats * self.px_per_beat,
                        ROW_H,
                    )
                    if rect.intersects(cr) and clip not in selected:
                        selected.append(clip)
            self.set_selection(selected)
            return
        if self._ruler_drag is not None:
            a, b = self._ruler_drag
            self._ruler_drag = None
            if abs(a - b) >= max(0.25, self.snap or 0.25):
                self.app.snapshot()
                self.app.set_song_loop_range(min(a, b), max(a, b), enable=True)
            else:
                self.seek.emit(self._ruler_seek if self._ruler_seek is not None else a)
            self._ruler_seek = None
            self.update()
            return
        if self._drag:
            changed = self._drag.get("snapshot", True)
            self._drag = None
            if changed:
                self.changed.emit()
            self.refresh()

    def select_clip(self, clip: Clip | None, additive: bool = False) -> None:
        if additive and clip:
            if clip in self.selected_clips:
                self.selected_clips.remove(clip)
            else:
                self.selected_clips.append(clip)
            self.selected_clip = self.selected_clips[-1] if self.selected_clips else None
        else:
            self.selected_clip = clip
            self.selected_clips = [clip] if clip else []
        self.selectionChanged.emit(self.selected_clip)
        self.use_selected_source()
        self.update()

    def set_selection(self, clips: list[Clip]) -> None:
        self.selected_clips = clips
        self.selected_clip = clips[-1] if clips else None
        self.selectionChanged.emit(self.selected_clip)
        self.use_selected_source()
        self.update()

    def use_selected_source(self):
        """Keep the last picked block available after clicking an empty lane."""
        if self.selected_clip is not None:
            self.place_template = self.selected_clip
            self.place = (self.selected_clip.kind, self.selected_clip.ref)

    def copy_selected(self, cut=False):
        targets = [
            (self.rows().index(row), replace(clip))
            for clip in self.selected_clips
            if (row := self.row_for_clip(clip))
        ]
        if not targets:
            return
        self.clipboard = targets
        if cut:
            self.delete_selected()

    def paste_clips(self, beat=None, row_index=None):
        if not self.clipboard:
            return []
        beat = self._snap(self.app.engine.beat if beat is None else beat)
        first_row = min(row for row, _ in self.clipboard)
        last_row = max(row for row, _ in self.clipboard)
        if last_row - first_row >= len(self.rows()):
            self.app.status.showMessage("Add tracks before pasting this selection", 3000)
            return []
        destination = self.paste_row if row_index is None else row_index
        destination = max(0, min(destination, len(self.rows()) - 1 - last_row + first_row))
        origin = min(clip.start_beat for _, clip in self.clipboard)
        # Clipboard contents can outlive Undo or a project load.
        patterns = {pattern.id for pattern in self.app.project.patterns}
        if any(
            (
                clip.ref not in patterns
                if clip.kind == "pattern"
                else clip.ref not in self.app.library.clips
            )
            for _, clip in self.clipboard
        ):
            self.app.status.showMessage("The copied source is no longer available", 3000)
            return []
        self.app.snapshot()
        copies = []
        for row, clip in self.clipboard:
            copy = replace(clip, id=uid(), start_beat=beat + clip.start_beat - origin)
            self.rows()[destination + row - first_row].clips.append(copy)
            copies.append(copy)
        self.set_selection(copies)
        self.changed.emit()
        self.refresh()
        return copies

    def nudge_selected(self, beats=0.0, rows=0):
        targets = [clip for clip in self.selected_clips if self.row_for_clip(clip)]
        if not targets:
            return
        origins = {clip.id: self.rows().index(self.row_for_clip(clip)) for clip in targets}
        beats = max(beats, -min(clip.start_beat for clip in targets))
        rows = max(-min(origins.values()), min(rows, len(self.rows()) - 1 - max(origins.values())))
        if not beats and not rows:
            return
        self.app.snapshot()
        for clip in targets:
            clip.start_beat += beats
            if rows:
                self.row_for_clip(clip).clips.remove(clip)
                self.rows()[origins[clip.id] + rows].clips.append(clip)
        self.changed.emit()
        self.refresh()

    def row_for_clip(self, clip: Clip):
        return next((row for row in self.rows() if clip in row.clips), None)

    def delete_selected(self) -> None:
        clips = list(self.selected_clips)
        if not clips:
            return
        self.app.snapshot()
        for clip in clips:
            row = self.row_for_clip(clip)
            if row:
                row.clips.remove(clip)
        self.select_clip(None)
        self.changed.emit()
        self.refresh()

    def duplicate_clip(self, clip: Clip | None = None, notify: bool = True) -> Clip | None:
        targets = [clip] if clip else list(self.selected_clips)
        targets = [target for target in targets if target and self.row_for_clip(target)]
        if not targets:
            return None
        if notify:
            self.app.snapshot()
        selection_start = min(target.start_beat for target in targets)
        selection_end = max(target.start_beat + target.length_beats for target in targets)
        shift = max(0.25, selection_end - selection_start)
        copies = []
        for target in targets:
            row = self.row_for_clip(target)
            copy = Clip(**{**target.__dict__, "id": uid(), "start_beat": target.start_beat + shift})
            row.clips.append(copy)
            copies.append(copy)
        if notify:
            self.set_selection(copies)
            self.changed.emit()
            self.refresh()
        return copies[-1]

    def _paint_at(self, row_index: int, beat: float) -> None:
        template = self._placement_template()
        if not 0 <= row_index < len(self.rows()) or template is None:
            return
        spacing = max(0.03125, template.length_beats)
        if self.snap > 0:
            spacing = max(self.snap, math.ceil(spacing / self.snap - 1e-9) * self.snap)
        starts = [beat]
        if self._paint_last and self._paint_last[0] == row_index:
            previous = self._paint_last[1]
            direction = 1 if beat >= previous else -1
            count = int((abs(beat - previous) + 1e-9) / spacing)
            starts = [previous + direction * spacing * i for i in range(1, count + 1)]
        row = self.rows()[row_index]
        for start in starts:
            self._paint_last = (row_index, start)
            if any(
                abs(c.start_beat - start) < 1e-6
                and c.kind == template.kind
                and c.ref == template.ref
                for c in row.clips
            ):
                continue
            if not self._paint_snapshot:
                self.app.snapshot()
                self._paint_snapshot = True
            clip = replace(template, id=uid(), start_beat=start)
            row.clips.append(clip)
            self.select_clip(clip)
            self.update()

    def split_clip(self, clip: Clip | None = None, beat: float | None = None) -> None:
        clip = clip or self.selected_clip
        row = self.row_for_clip(clip) if clip else None
        beat = self.app.engine.beat if beat is None else self._snap(beat)
        if (
            not clip
            or not row
            or not (clip.start_beat < beat < clip.start_beat + clip.length_beats)
        ):
            self.app.status.showMessage("put the playhead inside a clip to split it", 2500)
            return
        self.app.snapshot()
        left_beats = beat - clip.start_beat
        right = Clip(
            **{
                **clip.__dict__,
                "id": uid(),
                "start_beat": beat,
                "length_beats": clip.length_beats - left_beats,
            }
        )
        if clip.kind == "audio" and not clip.loop:
            elapsed = left_beats * (60.0 / self.app.project.bpm)
            right.offset += elapsed
            if clip.source_length > 0:
                right.source_length = max(0.001, clip.source_length - elapsed)
                clip.source_length = min(clip.source_length, elapsed)
        clip.length_beats = left_beats
        row.clips.append(right)
        self.select_clip(right)
        self.changed.emit()
        self.refresh()

    def _clip_menu(self, global_pos, clip: Clip, beat: float) -> None:
        menu = QMenu(self)
        menu.addAction("Copy · Ctrl+C", self.copy_selected)
        menu.addAction("Cut · Ctrl+X", lambda: self.copy_selected(cut=True))
        menu.addAction("Paste here · Ctrl+V", lambda: self.paste_clips(beat))
        menu.addAction("Split here", lambda: self.split_clip(clip, beat))
        menu.addAction("Duplicate", lambda: self.duplicate_clip(clip))
        if clip.kind == "pattern":
            menu.addAction("Edit pattern", lambda: self.app.open_pattern_clip(clip))
            menu.addAction("Make pattern unique", lambda: self.app.make_pattern_unique(clip))
        if clip.kind == "audio":
            menu.addAction(
                "Write notes with sample", lambda: self.app.sample_workflow.from_arrangement(clip)
            )
            loop = menu.addAction("Loop source")
            loop.setCheckable(True)
            loop.setChecked(clip.loop)
            loop.triggered.connect(lambda on: self.app.set_selected_clip_loop(on))
            reverse = menu.addAction("Reverse")
            reverse.setCheckable(True)
            reverse.setChecked(clip.reverse)
            reverse.triggered.connect(lambda on: self.app.set_selected_clip_reverse(on))
            menu.addAction("Export clip as WAV", self.app.export_selected_clip)
        muted = menu.addAction("Mute clip")
        muted.setCheckable(True)
        muted.setChecked(clip.mute)
        muted.triggered.connect(lambda on: self._set_clip_mute(clip, on))
        menu.addSeparator()
        menu.addAction("Delete", self.delete_selected)
        menu.exec(global_pos)

    def _set_clip_mute(self, clip: Clip, on: bool) -> None:
        self.app.snapshot()
        clip.mute = bool(on)
        self.changed.emit()
        self.update()

    def _row_menu(self, global_pos, row) -> None:
        menu = QMenu(self)
        if hasattr(self.app, "track_capture"):
            arm = menu.addAction("Arm for recording")
            arm.setCheckable(True)
            arm.setChecked(self.app.track_capture.armed_id == row.id)
            arm.triggered.connect(lambda: self.app.track_capture.arm(row))
        menu.addAction("Rename track", lambda: self.app.rename_song_row(row))
        mute = menu.addAction("Mute")
        mute.setCheckable(True)
        mute.setChecked(row.mute)
        mute.triggered.connect(lambda on: self._set_row_state(row, "mute", on))
        solo = menu.addAction("Solo")
        solo.setCheckable(True)
        solo.setChecked(row.solo)
        solo.triggered.connect(lambda on: self._set_row_state(row, "solo", on))
        colors = menu.addMenu("Track color")
        for color in TRACK_COLORS:
            swatch = QPixmap(14, 14)
            swatch.fill(QColor(color))
            action = colors.addAction(QIcon(swatch), color.upper())
            action.setData(color)
            action.triggered.connect(lambda _=False, c=color: self._set_row_state(row, "color", c))
        menu.exec(global_pos)

    def _set_row_state(self, row, field: str, value) -> None:
        self.app.snapshot()
        setattr(row, field, value)
        self.changed.emit()
        self.update()

    def keyPressEvent(self, ev):
        if ev.key() in TOOL_KEYS and not ev.modifiers():
            self.app.set_playlist_tool(TOOL_KEYS[ev.key()])
        elif ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif ev.key() == Qt.Key_A and ev.modifiers() & Qt.ControlModifier:
            self.set_selection([c for row in self.rows() for c in row.clips])
        elif ev.key() == Qt.Key_D and ev.modifiers() & Qt.ControlModifier:
            self.duplicate_clip()
        elif ev.key() == Qt.Key_C and ev.modifiers() & Qt.ControlModifier:
            self.copy_selected()
        elif ev.key() == Qt.Key_X and ev.modifiers() & Qt.ControlModifier:
            self.copy_selected(cut=True)
        elif ev.key() == Qt.Key_V and ev.modifiers() & Qt.ControlModifier:
            self.paste_clips()
        elif ev.key() in (Qt.Key_Left, Qt.Key_Right) and not ev.modifiers():
            self.nudge_selected(beats=(self.snap or 0.25) * (1 if ev.key() == Qt.Key_Right else -1))
        elif ev.key() in (Qt.Key_Up, Qt.Key_Down) and not ev.modifiers():
            self.nudge_selected(rows=1 if ev.key() == Qt.Key_Down else -1)
        elif ev.key() == Qt.Key_Return and self.selected_clip:
            if self.selected_clip.kind == "pattern":
                self.app.open_pattern_clip(self.selected_clip)
            else:
                self.app.sample_workflow.from_arrangement(self.selected_clip)
        elif ev.key() == Qt.Key_S and ev.modifiers() & Qt.ShiftModifier:
            self.split_clip()
        else:
            super().keyPressEvent(ev)

    # ── drops from Downloads / browser ──────────────────────
    @staticmethod
    def _audio_paths(mime) -> list[str]:
        if not mime.hasUrls():
            return []
        return [
            u.toLocalFile()
            for u in mime.urls()
            if (u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in AUDIO_EXT)
        ]

    @classmethod
    def _accepts_mime(cls, mime) -> bool:
        return (
            mime.hasFormat(RANGE_MIME)
            or mime.hasFormat("application/x-mpclab-clip")
            or bool(cls._audio_paths(mime))
        )

    def _set_drop_target(self, pos) -> bool:
        row_index = self.row_at(pos.y())
        if row_index < 0:
            self._drop_target = None
            self.update()
            return False
        self._drop_target = (row_index, self._snap(self.x_to_beat(pos.x())))
        self.update()
        return True

    def dragEnterEvent(self, ev):
        accepted = self._accepts_mime(ev.mimeData()) and self._set_drop_target(ev.position())
        if accepted:
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        accepted = self._accepts_mime(ev.mimeData()) and self._set_drop_target(ev.position())
        if accepted:
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragLeaveEvent(self, ev):
        self._drop_target = None
        self.update()
        ev.accept()

    def dropEvent(self, ev):
        target = self._drop_target
        self._drop_target = None
        self.update()
        if target is None:
            row_index = self.row_at(ev.position().y())
            if row_index < 0:
                ev.ignore()
                return
            target = (row_index, self._snap(self.x_to_beat(ev.position().x())))

        row_index, start_beat = target
        mime = ev.mimeData()

        if mime.hasFormat(RANGE_MIME):
            payload = sample_range(mime, self.app.library)
            if payload is None:
                ev.ignore()
                return
            self.place_sample_range(row_index, start_beat, *payload)
            ev.acceptProposedAction()
            return

        if mime.hasFormat("application/x-mpclab-clip"):
            clip_id = bytes(mime.data("application/x-mpclab-clip")).decode(errors="ignore")
            if clip_id not in self.app.library.clips:
                ev.ignore()
                return
            self.app.snapshot()
            placed = self._place_ref(row_index, start_beat, "audio", clip_id)
            self.select_clip(placed)
            name = self.app.library.clips[clip_id].name
            self.app.status.showMessage(f"{name} → {self.rows()[row_index].name}", 2500)
            ev.acceptProposedAction()
            return

        paths = self._audio_paths(mime)
        if not paths:
            ev.ignore()
            return
        imported = self.app.browser.import_paths(paths)
        if not imported:
            ev.ignore()
            return

        self.app.snapshot()
        beat = start_beat
        for meta in imported:
            placed = self._place_ref(row_index, beat, "audio", meta.id, notify=False)
            if placed:
                beat += placed.length_beats
                self.select_clip(placed)
        self.changed.emit()
        self.refresh()
        count = len(imported)
        label = "song" if count == 1 else "songs"
        self.app.status.showMessage(
            f"imported {count} {label} → {self.rows()[row_index].name}", 3500
        )
        ev.acceptProposedAction()

    def place_sample_range(self, row_index, start_beat, ref, start, end, *, snapshot=True):
        """Keep the source trim and its natural duration, even for tiny slices."""
        meta = self.app.library.clips.get(ref)
        if meta is None or not 0 <= row_index < len(self.rows()):
            return None
        if not 0 <= start < end <= meta.duration + 1e-6:
            return None
        self.app.library.audio(ref)
        if snapshot:
            self.app.snapshot()
        clip = Clip(
            kind="audio",
            ref=ref,
            start_beat=start_beat,
            length_beats=(end - start) * self.app.project.bpm / 60.0,
            offset=start,
            source_length=end - start,
            track=3,
        )
        self.rows()[row_index].clips.append(clip)
        self.select_clip(clip)
        self.changed.emit()
        self.refresh()
        self.app.status.showMessage(
            f"{meta.name} · {start:.3f}s → {end:.3f}s → {self.rows()[row_index].name}", 4000
        )
        return clip

    def _place_clip(self, row_index: int, start_beat: float):
        if not 0 <= row_index < len(self.rows()):
            return None
        template = self._placement_template()
        if template is None:
            return None
        self.app.snapshot()
        placed = replace(template, id=uid(), start_beat=start_beat)
        self.rows()[row_index].clips.append(placed)
        self.changed.emit()
        self.refresh()
        return placed

    def _placement_template(self):
        if not self.place:
            return None
        kind, ref = self.place
        if kind == "pattern":
            pattern = next((p for p in self.app.project.patterns if p.id == ref), None)
            if pattern is None:
                return None
            default = Clip(kind=kind, ref=ref, length_beats=pattern.length_beats)
        else:
            meta = self.app.library.clips.get(ref)
            if meta is None:
                return None
            self.app.library.audio(ref)
            default = Clip(
                kind=kind,
                ref=ref,
                length_beats=meta.duration * self.app.project.bpm / 60,
                source_length=meta.duration,
                track=3,
            )
        source = self.place_template
        return source if source and (source.kind, source.ref) == self.place else default

    def _place_ref(
        self, row_index: int, start_beat: float, kind: str, ref: str, notify: bool = True
    ) -> Clip | None:
        """Place one known pattern/library ref and return the created clip."""
        if row_index < 0 or row_index >= len(self.rows()):
            return None
        proj = self.app.project
        row = self.rows()[row_index]
        if kind == "pattern":
            pat = next((p for p in proj.patterns if p.id == ref), None)
            if not pat:
                return None
            placed = Clip(
                id=uid(),
                kind="pattern",
                ref=ref,
                start_beat=start_beat,
                length_beats=pat.length_beats,
                track=0,
            )
        else:
            clip_meta = self.app.library.clips.get(ref)
            if not clip_meta:
                return None
            self.app.library.audio(ref)  # warm the cache off the audio thread
            beats = clip_meta.duration / (60.0 / proj.bpm)
            placed = Clip(
                id=uid(),
                kind="audio",
                ref=ref,
                start_beat=start_beat,
                length_beats=max(0.25, round(beats, 3)),
                source_length=clip_meta.duration,
                track=3,
            )
        row.clips.append(placed)
        if notify:
            self.changed.emit()
            self.refresh()
        return placed

    def _tick(self):
        eng = self.app.engine
        beat = eng.beat if eng.playing else self._last_beat
        if abs(beat - self._last_beat) > 0.01:
            self._last_beat = beat
            self.update()

    # ── painting ─────────────────────────────────────────────
    def paintEvent(self, ev):
        p = QPainter(self)
        proj = self.app.project
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), q("canvas"))

        small = QFont(self.font())
        small.setPointSizeF(8.0)
        p.setFont(small)
        fm = QFontMetrics(small)

        last_beat = self.x_to_beat(w) + 4

        # The arrangement loop is drawn directly on the ruler and lanes. Drag
        # across the ruler to redefine it; the toolbar switch controls playback.
        loop_a, loop_b = proj.loop_start, proj.loop_end
        if self._ruler_drag is not None:
            loop_a, loop_b = sorted(self._ruler_drag)
        if loop_b > loop_a:
            lx = self.beat_to_x(loop_a)
            lw = (loop_b - loop_a) * self.px_per_beat
            p.fillRect(QRectF(lx, 0, lw, h), q("accent", 12 if self.app.engine.loop_song else 4))
            p.setPen(QPen(q("accent", 150), 1))
            p.drawLine(int(lx), 0, int(lx), h)
            p.drawLine(int(lx + lw), 0, int(lx + lw), h)

        # bar grid
        b = 0
        while b <= last_beat:
            x = self.beat_to_x(b)
            is_bar = b % 4 == 0
            if is_bar or self.px_per_beat >= 14:
                p.setPen(QPen(q("fg", 46 if is_bar else 16)))
                p.drawLine(int(x), RULER_H, int(x), h)
            b += 1

        # lanes
        any_row_solo = any(row.solo for row in self.rows())
        for i, row in enumerate(self.rows()):
            y = RULER_H + i * ROW_H
            row_disabled = row.mute or (any_row_solo and not row.solo)
            if self._drop_target and self._drop_target[0] == i:
                p.fillRect(QRectF(HEAD_W, y, w - HEAD_W, ROW_H), q("accent", 32))
            if i % 2:
                p.fillRect(QRectF(HEAD_W, y, w - HEAD_W, ROW_H), q("fg", 8))
            if row_disabled:
                p.fillRect(QRectF(HEAD_W, y, w - HEAD_W, ROW_H), q("rec", 22))
            p.setPen(QPen(q("fg", 30)))
            p.drawLine(0, int(y + ROW_H), w, int(y + ROW_H))

            # header
            p.fillRect(QRectF(0, y, HEAD_W, ROW_H), q("bg2"))
            track_color = row.color or TRACK_COLORS[i % len(TRACK_COLORS)]
            p.fillRect(QRectF(4, y, HEAD_W - 4, ROW_H), q(track_color, 24))
            p.fillRect(QRectF(0, y, 4, ROW_H), q(track_color))
            p.setPen(q("dim") if row_disabled else q("fg"))
            p.drawText(
                QRectF(10, y + 3, HEAD_W - 82, ROW_H / 2),
                Qt.AlignLeft | Qt.AlignVCenter,
                fm.elidedText(row.name, Qt.ElideRight, HEAD_W - 88),
            )
            p.setPen(q("rec") if row.mute else q("dim2"))
            p.drawText(
                QRectF(10, y + ROW_H / 2, HEAD_W - 62, ROW_H / 2 - 4),
                Qt.AlignLeft | Qt.AlignVCenter,
                "MUTED" if row.mute else f"{len(row.clips)} clips",
            )
            capture = getattr(self.app, "track_capture", None)
            armed = bool(capture and capture.armed_id == row.id)
            if armed:
                p.setPen(q("rec"))
                p.drawText(
                    QRectF(10, y + ROW_H / 2, HEAD_W - 82, ROW_H / 2 - 4),
                    Qt.AlignRight | Qt.AlignVCenter,
                    "REC" if capture.active else "ARMED",
                )
            for label, active, bx in (
                ("R", armed, HEAD_W - 65),
                ("M", row.mute, HEAD_W - 43),
                ("S", row.solo, HEAD_W - 21),
            ):
                br = QRectF(bx, y + 16, 19, 20)
                p.setBrush(q("rec" if label in ("R", "M") else "accent") if active else q("bg3"))
                p.setPen(QPen(q("line"), 1))
                p.drawRoundedRect(br, 3, 3)
                p.setPen(
                    q("on_rec")
                    if label in ("R", "M") and active
                    else q("on_accent")
                    if active
                    else q("dim")
                )
                p.drawText(br, Qt.AlignCenter, label)

            for clip in row.clips:
                self._draw_clip(p, clip, y + 3, ROW_H - 7, row_disabled or clip.mute, track_color)

            if capture and capture.active and capture.target.id == row.id:
                left = self.beat_to_x(capture.start_beat)
                right = self.beat_to_x(max(capture.start_beat, self.app.engine.beat))
                recording = QRectF(left, y + 3, max(4, right - left), ROW_H - 7)
                p.fillRect(recording, q("rec", 42))
                p.setPen(QPen(q("rec"), 1))
                p.drawRect(recording)
                p.save()
                p.setClipRect(recording)
                for beat, peak in capture.peaks:
                    x = self.beat_to_x(beat)
                    mid = y + ROW_H * 0.65
                    amplitude = peak * ROW_H * 0.25
                    p.drawLine(QPointF(x, mid - amplitude), QPointF(x, mid + amplitude))
                p.drawText(
                    recording.adjusted(6, 1, -2, -2),
                    Qt.AlignTop | Qt.AlignLeft,
                    "Recording · " + row.name,
                )
                p.restore()

        if self._drop_target:
            drop_x = self.beat_to_x(self._drop_target[1])
            p.setPen(QPen(q("accent"), 2))
            p.drawLine(int(drop_x), RULER_H, int(drop_x), h)

        # header column separator
        p.setPen(QPen(q("line")))
        p.drawLine(HEAD_W, 0, HEAD_W, h)

        # ruler
        p.fillRect(QRectF(0, 0, w, RULER_H), q("bg2"))
        if loop_b > loop_a:
            lx = self.beat_to_x(loop_a)
            lw = (loop_b - loop_a) * self.px_per_beat
            p.fillRect(QRectF(lx, 0, lw, RULER_H), q("accent", 52))
            p.setPen(QPen(q("accent_hi"), 2))
            p.drawLine(int(lx), 4, int(lx + lw), 4)
            p.setBrush(q("accent_hi"))
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(lx, 2), QPointF(lx + 7, 2), QPointF(lx, 10)]))
            p.drawPolygon(
                QPolygonF([QPointF(lx + lw, 2), QPointF(lx + lw - 7, 2), QPointF(lx + lw, 10)])
            )
        p.setPen(q("dim2"))
        b = 0
        while b <= last_beat:
            if b % 4 == 0:
                x = self.beat_to_x(b)
                p.drawLine(int(x), RULER_H - 7, int(x), RULER_H)
                if self.px_per_beat * 4 > 28:
                    p.drawText(
                        QRectF(x + 3, 0, 40, RULER_H - 6),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        str(b // 4 + 1),
                    )
            b += 4

        if self._marquee is not None:
            a, b = self._marquee
            box = QRectF(a, b).normalized()
            p.fillRect(box, q("accent", 30))
            p.setPen(QPen(q("accent_hi"), 1, Qt.DashLine))
            p.drawRect(box)

        # playhead
        beat = self.app.engine.beat
        x = self.beat_to_x(beat)
        p.setPen(QPen(q("ok"), 1.5))
        p.drawLine(int(x), 0, int(x), h)
        p.setBrush(q("ok"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)]))

    def _draw_clip(self, p: QPainter, clip: Clip, y: float, h: float, muted: bool, color=None):
        proj = self.app.project
        x = self.beat_to_x(clip.start_beat)
        w = max(5.0, clip.length_beats * self.px_per_beat)
        rect = QRectF(x, y, w, h)
        is_pattern = clip.kind == "pattern"

        base = (
            (q(color).lighter(175) if is_light() else q(color).darker(235))
            if color
            else q("clip_pat" if is_pattern else "clip_aud")
        )
        if muted:
            base = q("clip_muted")
        p.setBrush(base)
        selected = clip in self.selected_clips
        border = (
            q("accent_hi")
            if selected
            else q(color)
            if color
            else q("clip_pat_line" if is_pattern else "clip_aud_line")
        )
        p.setPen(QPen(border, 2 if clip is self._hover_clip or selected else 1))
        p.drawRoundedRect(rect, 3, 3)
        if selected:
            p.setBrush(q("accent_hi"))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x, y + h / 2 - 8, 5, 16), 2, 2)
            p.drawRoundedRect(QRectF(x + w - 5, y + h / 2 - 8, 5, 16), 2, 2)

        p.save()
        p.setClipRect(rect)

        if is_pattern:
            pat = next((pp for pp in proj.patterns if pp.id == clip.ref), None)
            if pat and pat.steps:
                lanes = sorted(pat.steps.keys())
                lane_h = max(1.5, (h - 13) / len(lanes))
                p.setBrush(q("clip_pat_ink", 215))
                p.setPen(Qt.NoPen)
                pat_beats = pat.length_beats
                reps = int(clip.length_beats / pat_beats) + 1
                step_w = max(1.5, self.px_per_beat / pat.div - 0.5)
                for li, lane in enumerate(lanes):
                    for step in pat.steps[lane]:
                        for r in range(reps):
                            beat = r * pat_beats + step / pat.div
                            if beat >= clip.length_beats:
                                break
                            p.drawRect(
                                QRectF(
                                    x + beat * self.px_per_beat,
                                    y + 12 + li * lane_h,
                                    step_w,
                                    max(1.5, lane_h - 0.8),
                                )
                            )
            if pat and pat.notes:
                low = min(n.pitch for n in pat.notes) - 1
                high = max(n.pitch for n in pat.notes) + 1
                p.setPen(Qt.NoPen)
                p.setBrush(q("clip_pat_ink", 210))
                for repeat in range(int(clip.length_beats / pat.length_beats) + 1):
                    for note in pat.notes:
                        beat = repeat * pat.length_beats + note.start
                        if beat >= clip.length_beats:
                            continue
                        duration = min(
                            note.duration, pat.length_beats - note.start, clip.length_beats - beat
                        )
                        if duration <= 0:
                            continue
                        note_y = y + 15 + (high - note.pitch) / (high - low) * (h - 21)
                        p.drawRoundedRect(
                            QRectF(
                                x + beat * self.px_per_beat,
                                note_y,
                                max(2, duration * self.px_per_beat - 1),
                                2.5,
                            ),
                            1,
                            1,
                        )
        else:
            peaks = self.app.library.peaks(clip.ref)
            meta = self.app.library.clips.get(clip.ref)
            if peaks is not None and meta and meta.duration > 0:
                source_secs = clip.source_length or max(0.0, meta.duration - clip.offset)
                source_secs = min(source_secs, max(0.0, meta.duration - clip.offset))
                source_frac = source_secs / meta.duration
                if clip.reverse:
                    peaks = peaks[::-1]
                    a = max(0.0, (meta.duration - clip.offset - source_secs) / meta.duration)
                else:
                    a = clip.offset / meta.duration
                arranged_secs = clip.length_beats * (60.0 / proj.bpm)
                if clip.loop and source_secs > 0:
                    cursor = 0.0
                    while cursor < arranged_secs - 1e-9:
                        seconds = min(source_secs, arranged_secs - cursor)
                        rw = seconds / arranged_secs * w
                        rx = x + cursor / arranged_secs * w
                        draw_peaks(
                            p,
                            peaks,
                            QRectF(rx, y + 11, rw, h - 13),
                            max(0.0, a),
                            min(1.0, a + source_frac),
                            q("clip_aud_ink", 215),
                        )
                        if cursor > 0:
                            p.setPen(QPen(q("accent_hi", 145), 1))
                            p.drawLine(int(rx), int(y + 11), int(rx), int(y + h))
                        cursor += source_secs
                else:
                    span = min(source_secs, arranged_secs) / meta.duration
                    draw_peaks(
                        p,
                        peaks,
                        QRectF(x, y + 11, w, h - 13),
                        max(0.0, a),
                        min(1.0, a + span),
                        q("clip_aud_ink", 215),
                    )

        p.fillRect(QRectF(x, y, w, 11), q("canvas", 150))
        name = (
            next((pp.name for pp in proj.patterns if pp.id == clip.ref), "pattern")
            if is_pattern
            else (
                self.app.library.clips.get(clip.ref).name
                if clip.ref in self.app.library.clips
                else "audio"
            )
        )
        if clip.kind == "audio":
            name = ("⟳ " if clip.loop else "") + ("↶ " if clip.reverse else "") + name
        elif pat := next((pp for pp in proj.patterns if pp.id == clip.ref), None):
            repeats = clip.length_beats / max(0.25, pat.length_beats)
            if repeats > 1.01:
                name += f"  ×{repeats:g}"
        if clip.mute:
            name = "MUTED · " + name
        p.setPen(q("clip_title_ink"))
        p.drawText(QRectF(x + 3, y, w - 6, 11), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.restore()
