"""Playlist edits.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
from dataclasses import replace
import math
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QMenu
from ..model import Clip, uid
from .theme import TRACK_COLORS


def copy_selected(owner, cut=False):
    targets = [
        (owner.rows().index(row), replace(clip))
        for clip in owner.selected_clips
        if (row := owner.row_for_clip(clip))
    ]
    if not targets:
        return
    owner.clipboard = targets
    if cut:
        owner.delete_selected()


def paste_clips(owner, beat=None, row_index=None):
    if not owner.clipboard:
        return []
    beat = owner._snap(owner.app.engine.beat if beat is None else beat)
    first_row = min(row for row, _ in owner.clipboard)
    last_row = max(row for row, _ in owner.clipboard)
    if last_row - first_row >= len(owner.rows()):
        owner.app.status.showMessage("Add tracks before pasting this selection", 3000)
        return []
    destination = owner.paste_row if row_index is None else row_index
    destination = max(0, min(destination, len(owner.rows()) - 1 - last_row + first_row))
    origin = min(clip.start_beat for _, clip in owner.clipboard)
    # Clipboard contents can outlive Undo or a project load.
    patterns = {pattern.id for pattern in owner.app.project.patterns}
    if any(
        (
            clip.ref not in patterns
            if clip.kind == "pattern"
            else clip.ref not in owner.app.library.clips
        )
        for _, clip in owner.clipboard
    ):
        owner.app.status.showMessage("The copied source is no longer available", 3000)
        return []
    owner.app.snapshot()
    copies = []
    for row, clip in owner.clipboard:
        copy = replace(clip, id=uid(), start_beat=beat + clip.start_beat - origin)
        owner.rows()[destination + row - first_row].clips.append(copy)
        copies.append(copy)
    owner.set_selection(copies)
    owner.changed.emit()
    owner.refresh()
    return copies


def nudge_selected(owner, beats=0.0, rows=0):
    targets = [clip for clip in owner.selected_clips if owner.row_for_clip(clip)]
    if not targets:
        return
    origins = {clip.id: owner.rows().index(owner.row_for_clip(clip)) for clip in targets}
    beats = max(beats, -min(clip.start_beat for clip in targets))
    rows = max(-min(origins.values()), min(rows, len(owner.rows()) - 1 - max(origins.values())))
    if not beats and not rows:
        return
    owner.app.snapshot()
    for clip in targets:
        clip.start_beat += beats
        if rows:
            owner.row_for_clip(clip).clips.remove(clip)
            owner.rows()[origins[clip.id] + rows].clips.append(clip)
    owner.changed.emit()
    owner.refresh()


def row_for_clip(owner, clip: Clip):
    return next((row for row in owner.rows() if clip in row.clips), None)


def delete_selected(owner) -> None:
    clips = list(owner.selected_clips)
    if not clips:
        return
    owner.app.snapshot()
    for clip in clips:
        row = owner.row_for_clip(clip)
        if row:
            row.clips.remove(clip)
    owner.select_clip(None)
    owner.changed.emit()
    owner.refresh()


def duplicate_clip(owner, clip: Clip | None = None, notify: bool = True) -> Clip | None:
    targets = [clip] if clip else list(owner.selected_clips)
    targets = [target for target in targets if target and owner.row_for_clip(target)]
    if not targets:
        return None
    if notify:
        owner.app.snapshot()
    selection_start = min(target.start_beat for target in targets)
    selection_end = max(target.start_beat + target.length_beats for target in targets)
    shift = max(0.25, selection_end - selection_start)
    copies = []
    for target in targets:
        row = owner.row_for_clip(target)
        copy = Clip(**{**target.__dict__, "id": uid(), "start_beat": target.start_beat + shift})
        row.clips.append(copy)
        copies.append(copy)
    if notify:
        owner.set_selection(copies)
        owner.changed.emit()
        owner.refresh()
    return copies[-1]


def _paint_at(owner, row_index: int, beat: float) -> None:
    template = owner._placement_template()
    if not 0 <= row_index < len(owner.rows()) or template is None:
        return
    spacing = max(0.03125, template.length_beats)
    if owner.snap > 0:
        spacing = max(owner.snap, math.ceil(spacing / owner.snap - 1e-9) * owner.snap)
    starts = [beat]
    if owner._paint_last and owner._paint_last[0] == row_index:
        previous = owner._paint_last[1]
        direction = 1 if beat >= previous else -1
        count = int((abs(beat - previous) + 1e-9) / spacing)
        starts = [previous + direction * spacing * i for i in range(1, count + 1)]
    row = owner.rows()[row_index]
    for start in starts:
        owner._paint_last = (row_index, start)
        if any(
            abs(c.start_beat - start) < 1e-6 and c.kind == template.kind and c.ref == template.ref
            for c in row.clips
        ):
            continue
        if not owner._paint_snapshot:
            owner.app.snapshot()
            owner._paint_snapshot = True
        clip = replace(template, id=uid(), start_beat=start)
        row.clips.append(clip)
        owner.select_clip(clip)
        owner.update()


def split_clip(owner, clip: Clip | None = None, beat: float | None = None) -> None:
    clip = clip or owner.selected_clip
    row = owner.row_for_clip(clip) if clip else None
    beat = owner.app.engine.beat if beat is None else owner._snap(beat)
    if not clip or not row or not (clip.start_beat < beat < clip.start_beat + clip.length_beats):
        owner.app.status.showMessage("put the playhead inside a clip to split it", 2500)
        return
    owner.app.snapshot()
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
        elapsed = left_beats * (60.0 / owner.app.project.bpm)
        right.offset += elapsed
        if clip.source_length > 0:
            right.source_length = max(0.001, clip.source_length - elapsed)
            clip.source_length = min(clip.source_length, elapsed)
    clip.length_beats = left_beats
    row.clips.append(right)
    owner.select_clip(right)
    owner.changed.emit()
    owner.refresh()


def _clip_menu(owner, global_pos, clip: Clip, beat: float) -> None:
    menu = QMenu(owner)
    menu.addAction("Copy · Ctrl+C", owner.copy_selected)
    menu.addAction("Cut · Ctrl+X", lambda: owner.copy_selected(cut=True))
    menu.addAction("Paste here · Ctrl+V", lambda: owner.paste_clips(beat))
    menu.addAction("Split here", lambda: owner.split_clip(clip, beat))
    menu.addAction("Duplicate", lambda: owner.duplicate_clip(clip))
    if clip.kind == "pattern":
        menu.addAction("Edit pattern", lambda: owner.app.open_pattern_clip(clip))
        menu.addAction("Make pattern unique", lambda: owner.app.make_pattern_unique(clip))
    if clip.kind == "audio":
        menu.addAction("Open in Autotune", lambda: owner.app.open_vocal_clip(clip))
        menu.addAction(
            "Write notes with sample", lambda: owner.app.sample_workflow.from_arrangement(clip)
        )
        loop = menu.addAction("Loop source")
        loop.setCheckable(True)
        loop.setChecked(clip.loop)
        loop.triggered.connect(lambda on: owner.app.set_selected_clip_loop(on))
        reverse = menu.addAction("Reverse")
        reverse.setCheckable(True)
        reverse.setChecked(clip.reverse)
        reverse.triggered.connect(lambda on: owner.app.set_selected_clip_reverse(on))
        menu.addAction("Export clip as WAV", owner.app.export_selected_clip)
    muted = menu.addAction("Mute clip")
    muted.setCheckable(True)
    muted.setChecked(clip.mute)
    muted.triggered.connect(lambda on: owner._set_clip_mute(clip, on))
    menu.addSeparator()
    menu.addAction("Delete", owner.delete_selected)
    menu.exec(global_pos)


def _set_clip_mute(owner, clip: Clip, on: bool) -> None:
    owner.app.snapshot()
    clip.mute = bool(on)
    owner.changed.emit()
    owner.update()


def _row_menu(owner, global_pos, row) -> None:
    menu = QMenu(owner)
    if hasattr(owner.app, "track_capture"):
        arm = menu.addAction("Arm for recording")
        arm.setCheckable(True)
        arm.setChecked(owner.app.track_capture.armed_id == row.id)
        arm.triggered.connect(lambda: owner.app.track_capture.arm(row))
    menu.addAction("Rename track", lambda: owner.app.rename_song_row(row))
    mute = menu.addAction("Mute")
    mute.setCheckable(True)
    mute.setChecked(row.mute)
    mute.triggered.connect(lambda on: owner._set_row_state(row, "mute", on))
    solo = menu.addAction("Solo")
    solo.setCheckable(True)
    solo.setChecked(row.solo)
    solo.triggered.connect(lambda on: owner._set_row_state(row, "solo", on))
    colors = menu.addMenu("Track color")
    for color in TRACK_COLORS:
        swatch = QPixmap(14, 14)
        swatch.fill(QColor(color))
        action = colors.addAction(QIcon(swatch), color.upper())
        action.setData(color)
        action.triggered.connect(lambda _=False, c=color: owner._set_row_state(row, "color", c))
    menu.exec(global_pos)


def _set_row_state(owner, row, field: str, value) -> None:
    owner.app.snapshot()
    setattr(row, field, value)
    owner.changed.emit()
    owner.update()
