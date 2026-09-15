"""Playlist / arrangement timeline — pattern blocks and audio clips on lanes."""

from __future__ import annotations

from pathlib import Path

from .window_client import WindowClient

from PySide6.QtCore import Qt, QRectF, QSize, QPointF, Signal, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QMenu

from ..library import AUDIO_EXT
from ..model import Clip
from .sample_drag import RANGE_MIME
from ..timeline_markers import marker_items

from . import playlist_rendering, playlist_edits, playlist_drop
from .playlist_geometry import (
    HEAD_W as HEAD_W,
    ROW_H as ROW_H,
    RULER_H as RULER_H,
    TOOL_KEYS as TOOL_KEYS,
)


# Tool shortcuts: FL Studio's letters, and this app's original numbers.  The
# main window handles these too, for when the Playlist does not hold focus —
# both read this one table.


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

    def timeline_markers(self):
        # Marker edits replace their validated sidecar atomically. Cache its
        # immutable entries so transport repaints do not revalidate thousands
        # of names and colors on every frame. Project replacement invalidates it.
        project = self.app.project
        state = getattr(project, "timeline_markers", None)
        if (
            getattr(self, "_marker_project", None) is not project
            or getattr(self, "_marker_state", None) is not state
        ):
            self._marker_project = project
            self._marker_state = state
            self._marker_items = marker_items(project)
        return self._marker_items

    def minimumSizeHint(self) -> QSize:
        marker_end = max(
            (marker.end_beat or marker.start_beat for marker in self.timeline_markers()),
            default=0,
        )
        length = max(
            self.app.project.song_end() + 32,
            self.app.project.loop_end + 8,
            marker_end + 8,
            64,
        )
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
        return playlist_edits.copy_selected(self, cut)

    def paste_clips(self, beat=None, row_index=None):
        return playlist_edits.paste_clips(self, beat, row_index)

    def nudge_selected(self, beats=0.0, rows=0):
        return playlist_edits.nudge_selected(self, beats, rows)

    def row_for_clip(self, clip: Clip):
        return playlist_edits.row_for_clip(self, clip)

    def delete_selected(self) -> None:
        return playlist_edits.delete_selected(self)

    def duplicate_clip(self, clip: Clip | None = None, notify: bool = True) -> Clip | None:
        return playlist_edits.duplicate_clip(self, clip, notify)

    def _paint_at(self, row_index: int, beat: float) -> None:
        return playlist_edits._paint_at(self, row_index, beat)

    def split_clip(self, clip: Clip | None = None, beat: float | None = None) -> None:
        return playlist_edits.split_clip(self, clip, beat)

    def _clip_menu(self, global_pos, clip: Clip, beat: float) -> None:
        return playlist_edits._clip_menu(self, global_pos, clip, beat)

    def _set_clip_mute(self, clip: Clip, on: bool) -> None:
        return playlist_edits._set_clip_mute(self, clip, on)

    def _row_menu(self, global_pos, row) -> None:
        return playlist_edits._row_menu(self, global_pos, row)

    def _set_row_state(self, row, field: str, value) -> None:
        return playlist_edits._set_row_state(self, row, field, value)

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
        return playlist_drop._set_drop_target(self, pos)

    def dragEnterEvent(self, ev):
        return playlist_drop.dragEnterEvent(self, ev)

    def dragMoveEvent(self, ev):
        return playlist_drop.dragMoveEvent(self, ev)

    def dragLeaveEvent(self, ev):
        return playlist_drop.dragLeaveEvent(self, ev)

    def dropEvent(self, ev):
        return playlist_drop.dropEvent(self, ev)

    def place_sample_range(self, row_index, start_beat, ref, start, end, *, snapshot=True):
        return playlist_drop.place_sample_range(
            self, row_index, start_beat, ref, start, end, snapshot=snapshot
        )

    def _place_clip(self, row_index: int, start_beat: float):
        return playlist_drop._place_clip(self, row_index, start_beat)

    def _placement_template(self):
        return playlist_drop._placement_template(self)

    def _place_ref(
        self, row_index: int, start_beat: float, kind: str, ref: str, notify: bool = True
    ) -> Clip | None:
        return playlist_drop._place_ref(self, row_index, start_beat, kind, ref, notify)

    def _tick(self):
        eng = self.app.engine
        beat = eng.beat if eng.playing else self._last_beat
        if abs(beat - self._last_beat) > 0.01:
            self._last_beat = beat
            self.update()

    # ── painting ─────────────────────────────────────────────
    def paintEvent(self, ev):
        return playlist_rendering.paintEvent(self, ev)

    def _draw_clip(self, p: QPainter, clip: Clip, y: float, h: float, muted: bool, color=None):
        return playlist_rendering._draw_clip(self, p, clip, y, h, muted, color)
