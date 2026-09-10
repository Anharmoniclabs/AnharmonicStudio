"""Arrangement actions.

Functions receive the workstation coordinator explicitly; Qt ownership and
project state stay with that coordinator. This module owns only its named domain.
"""

from __future__ import annotations
import os
import time
import numpy as np
import soundfile as sf
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QMessageBox,
    QApplication,
    QInputDialog,
)
from ..model import (
    Row,
    uid,
)
from .. import separate
from .playlist import ROW_H, RULER_H


def set_playlist_focus(window, on: bool):
    """Give the arrangement the window without destroying either sidebar."""
    window._playlist_focus = bool(on)
    if on:
        window._normal_split_sizes = window.main_splitter.sizes()
        window._focus_panel_visibility = (
            not window.browser_frame.isHidden(),
            not window.pad_side.isHidden(),
        )
        # Focus mode exists so the timeline can sit beside another window.
        # A maximized window ignores resize(), so leave that state — and
        # remember it, to put the workspace back the way it was on exit.
        window._was_maximized = window.isMaximized()
        if window._was_maximized:
            window.showNormal()
        window.browser_frame.hide()
        window.pad_side.hide()
        window.tabs.setCurrentIndex(2)
    else:
        visible = getattr(window, "_focus_panel_visibility", (True, True))
        window.browser_frame.setVisible(visible[0])
        window.pad_side.setVisible(visible[1])
        window.main_splitter.setSizes(window._normal_split_sizes or [304, 1066, 300])
        if getattr(window, "_was_maximized", False):
            window.showMaximized()
            window._was_maximized = False
    for widget in window.transport_focus_hidden:
        widget.setVisible(not on)
    window._sync_compact_playlist_ui()
    window.btn_playlist_focus.setText("⛶ EXIT" if on else "⛶ FOCUS")
    # Showing and hiding widgets only *queues* the layout requests that
    # move the window's minimum size.  Flush them here, after every
    # visibility change above, or the first resize following a focus-mode
    # toggle — a drag of the window edge, or a tiling window manager
    # placing us — is still clamped by the full-workspace minimum.
    QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
    window.status.showMessage("Playlist focus mode" if on else "sidebars restored", 1800)


def _sync_compact_playlist_ui(window):
    """Keep the Playlist's primary controls usable in narrow/focused windows."""
    if not hasattr(window, "playlist_hint"):
        return
    focused = bool(getattr(window, "_playlist_focus", False))
    narrow = window.width() < 1200
    window.playlist_hint.setVisible(not focused and window.width() >= 1450)
    window.playlist_place_label.setVisible(not focused and not narrow)
    window.playlist_snap_label.setVisible(not focused and not narrow)
    window.playlist_zoom_label.setVisible(not focused and not narrow)
    window.zoom.setVisible(not focused and not narrow)
    window.place_box.setMinimumWidth(120 if focused else (140 if narrow else 190))


def set_playlist_tool(window, tool: str):
    if hasattr(window, "playlist"):
        window.playlist.tool = tool
        window.playlist.update_cursor()
    button = window.playlist_tool_buttons.get(tool)
    if button and not button.isChecked():
        button.setChecked(True)
    window.status.showMessage(f"Playlist tool · {tool}", 1200)


def _playlist_selection_changed(window, clip):
    if clip is not None:
        source = (clip.kind, clip.ref)
        if clip.kind == "pattern" and not any(
            window.place_box.itemData(i) == source for i in range(window.place_box.count())
        ):
            window._refresh_place_box()
        window.place_box.blockSignals(True)
        window.place_box.setCurrentIndex(-1)
        for i in range(window.place_box.count()):
            if window.place_box.itemData(i) == source:
                window.place_box.setCurrentIndex(i)
                break
        window.place_box.blockSignals(False)
    if clip is not None and hasattr(window, "track_inspector"):
        row = window.playlist.row_for_clip(clip)
        if row is not None:
            window.track_inspector.select_row(row)
    window.playlist_clip_scroll.setVisible(bool(clip))
    window.playlist_clip_tools.setVisible(bool(clip))
    audio = bool(clip and clip.kind == "audio")
    pattern = bool(clip and clip.kind == "pattern")
    window.btn_clip_unique.setVisible(pattern)
    window.btn_clip_notes.setVisible(audio)
    window.playlist_clip_name.setText(
        (
            window.library.clips.get(clip.ref).name
            if audio and clip.ref in window.library.clips
            else next(
                (p.name for p in window.project.patterns if clip and p.id == clip.ref),
                "NO CLIP SELECTED",
            )
        )
    )
    if pattern:
        count = sum(
            other.kind == "pattern" and other.ref == clip.ref
            for row in window.project.rows
            for other in row.clips
        )
        if count > 1:
            window.playlist_clip_name.setText(
                f"{window.playlist_clip_name.text()} · shared by {count} clips"
            )
    window.btn_clip_loop.blockSignals(True)
    window.btn_clip_reverse.blockSignals(True)
    window.clip_gain.blockSignals(True)
    window.clip_track.blockSignals(True)
    window.clip_crossfade.blockSignals(True)
    window.btn_clip_loop.setChecked(bool(audio and clip.loop))
    window.btn_clip_reverse.setChecked(bool(audio and clip.reverse))
    window.clip_gain.setValue(round((clip.gain if clip else 1.0) * 100))
    window.clip_track.setCurrentIndex(clip.track if clip else 0)
    window.clip_crossfade.setValue((clip.loop_crossfade if audio else 0.0) * 1000.0)
    window.btn_clip_loop.blockSignals(False)
    window.btn_clip_reverse.blockSignals(False)
    window.clip_gain.blockSignals(False)
    window.clip_track.blockSignals(False)
    window.clip_crossfade.blockSignals(False)
    window.btn_clip_loop.setEnabled(audio)
    window.btn_clip_reverse.setEnabled(audio)
    window.clip_gain.setEnabled(bool(clip))
    window.clip_track.setEnabled(audio)
    window.clip_crossfade.setEnabled(audio)


def rename_song_row(window, row):
    name, ok = QInputDialog.getText(window, "Rename Playlist track", "Track name:", text=row.name)
    if ok and name.strip():
        window.snapshot()
        row.name = name.strip()
        window.playlist.update()


def set_selected_clip_loop(window, on: bool):
    clip = window.playlist.selected_clip
    if not clip or clip.kind != "audio" or clip.loop == bool(on):
        return
    window.snapshot()
    clip.loop = bool(on)
    window.playlist.update()
    window.status.showMessage(
        "audio source will repeat to fill the block" if on else "audio source loop off", 2200
    )


def set_selected_clip_reverse(window, on: bool):
    clip = window.playlist.selected_clip
    if not clip or clip.kind != "audio" or clip.reverse == bool(on):
        return
    window.snapshot()
    if on:
        window.library.reversed_audio(clip.ref)
    clip.reverse = bool(on)
    window.playlist.update()


def _selected_clip_gain(window, value: int):
    clip = window.playlist.selected_clip
    if clip and clip.gain != value / 100.0:
        window.snapshot()
        clip.gain = value / 100.0
        window._set_dirty(True)
        window.playlist.update()


def _selected_clip_crossfade(window, value: float):
    clip = window.playlist.selected_clip
    crossfade = max(0.0, float(value) / 1000.0)
    if clip and clip.kind == "audio" and clip.loop_crossfade != crossfade:
        window.snapshot()
        clip.loop_crossfade = crossfade
        window._set_dirty(True)


def _selected_clip_track(window, index: int):
    clip = window.playlist.selected_clip
    track = window.clip_track.itemData(index)
    if clip and clip.kind == "audio" and track is not None and clip.track != track:
        window.snapshot()
        clip.track = int(track)
        window._set_dirty(True)


def export_selected_clip(window):
    clip = window.playlist.selected_clip
    if not clip or clip.kind != "audio":
        window.status.showMessage("select an audio clip first", 2200)
        return
    data = window.library.audio(clip.ref)
    if data is None:
        QMessageBox.warning(window, "Save WAV failed", "The source audio is missing.")
        return
    s0 = max(0, min(len(data), int(clip.offset * window.engine.sr)))
    source_frames = (
        int(clip.source_length * window.engine.sr) if clip.source_length > 0 else len(data) - s0
    )
    segment = data[s0 : min(len(data), s0 + source_frames)]
    if not len(segment):
        QMessageBox.warning(window, "Save WAV failed", "This clip has no audio in its trim range.")
        return
    if clip.reverse:
        segment = segment[::-1]
    arranged = max(1, int(clip.length_beats * (60.0 / window.project.bpm) * window.engine.sr))
    if clip.loop:
        copies = int(np.ceil(arranged / len(segment)))
        segment = np.tile(segment, (copies, 1))[:arranged]
    else:
        segment = segment[:arranged]
    segment = np.clip(segment * clip.gain, -1.0, 1.0)
    source_name = window.library.clips.get(clip.ref).name
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = window.exports_dir / f"{source_name}-clip-{stamp}.wav"
    sf.write(str(out), segment, window.engine.sr, subtype="PCM_24")
    window.status.showMessage(f"saved clip WAV → {out}", 8000)


def prepare_vocal_recording(window):
    """Prepare dry Song capture; never open a device until Record is pressed."""
    if window.track_capture.busy:
        window.show_tab(2)
        return
    row = next((r for r in window.project.rows if r.id == window.track_capture.armed_id), None)
    if row is None or row.record_source != "audio":
        row = window.track_inspector.row()
    if row is None or row.record_source != "audio":
        window.add_vocal_track()
        return
    if window.engine.mode != "song":
        position = window.engine.beat
        window.set_mode("song")
        window.engine.set_position(position)
    window.show_tab(2)
    window.track_inspector.select_row(row)
    if window.track_capture.armed_id != row.id:
        window.track_capture.arm(row)
    window.status.showMessage(
        "Microphone track armed · choose input, then press Record · Stop saves the waveform in Song",
        7000,
    )


def add_vocal_track(window):
    if window.track_capture.busy:
        window.status.showMessage("Finish the current recording before adding a vocal track", 3500)
        return
    window.snapshot()
    number = 1 + sum(r.name.startswith("Vocal ") for r in window.project.rows)
    row = Row(name=f"Vocal {number}", record_source="audio", record_track=3)
    index = next(
        (i for i, r in enumerate(window.project.rows) if not r.clips), len(window.project.rows)
    )
    window.project.rows.insert(index, row)
    window.track_inspector.select_row(row)
    window.track_capture.arm(row)
    window.playlist.refresh()
    window.prepare_vocal_recording()
    window.song_scroll.ensureVisible(0, RULER_H + index * ROW_H)


def open_vocal_clip(window, clip=None):
    clip = clip if clip is not None else window.playlist.selected_clip
    if clip is None or clip.kind != "audio":
        window.status.showMessage("Select a recorded audio clip in Song to open in Autotune", 4500)
        return
    window.vocal_panel.open_arranged_take(clip)
    window.show_tab(5)


def add_song_row(window):
    window.snapshot()
    row = Row(name=f"TRACK {len(window.project.rows) + 1}")
    window.project.rows.append(row)
    window.track_inspector.select_row(row)
    window.track_controls_button.setChecked(True)
    window.playlist.refresh()
    window.song_scroll.ensureVisible(
        int(window.playlist.beat_to_x(0)), RULER_H + len(window.project.rows) * ROW_H
    )


def _ensure_playlist_rows(window, minimum: int = 12):
    """Give the Playlist a useful FL-style lane stack, including old projects."""
    while len(window.project.rows) < minimum:
        window.project.rows.append(Row(name=f"TRACK {len(window.project.rows) + 1}"))


def _library_changed(window):
    window._refresh_place_box()
    window.pads.update()
    if hasattr(window, "vocal_panel"):
        window.vocal_panel.refresh_takes()


def adopt_stems(window, job) -> list[str]:
    """Move a finished Demucs job into the normal clip library."""
    window.snapshot()
    imported: list[str] = []
    folder = window.separator.out_root / job.id
    preferred = list(separate.MODELS.get(job.model, {}).get("stems", ()))
    names = preferred + sorted(name for name in job.stems if name not in preferred)
    for stem in names:
        filename = job.stems.get(stem)
        if not filename:
            continue
        source = folder / filename
        if not source.is_file():
            continue
        clip = window.library.import_file(
            source,
            name=f"{job.name} · {stem.upper()}",
            kind="stem",
            parent=job.source_clip,
            stem=stem,
            move=True,
        )
        imported.append(clip.id)
    if not imported:
        window.discard_snapshot()
        raise RuntimeError("the stem output folder contained no readable audio")
    if folder.exists():
        archived = window.library.root / "_trash" / "stem-jobs" / f"{job.id}-{uid()}"
        archived.parent.mkdir(parents=True, exist_ok=True)
        os.replace(folder, archived)
    return imported
