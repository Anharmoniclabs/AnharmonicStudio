"""Window sampling.

Functions receive the workstation coordinator explicitly; Qt ownership and
project state stay with that coordinator. This module owns only its named domain.
"""

from __future__ import annotations
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
)
from ..model import (
    Row,
    Pad,
    PADS_PER_BANK,
    NPADS,
    map_sample_range,
)
from ..synth import render_patch
from ..workflow import four_bar_phrase, pattern_arrangement_target
from .playlist import ROW_H, RULER_H


def set_bank(window, bank: int):
    window.pads.bank = bank
    window.step_grid.bank = bank
    for i, b in enumerate(window.bank_buttons):
        b.setChecked(i == bank)
    window.pads.update()
    # Which lanes are worth showing depends on the bank, so the grid has to
    # be re-measured rather than merely repainted.
    window.step_grid.refresh()


def select_pad(window, gi: int):
    window.pads.selected = gi
    if hasattr(window, "sample_target"):
        window.sample_target.blockSignals(True)
        window.sample_target.setCurrentIndex(gi)
        window.sample_target.blockSignals(False)
    window.pad_inspector.set_pad(gi)
    if hasattr(window, "map_selection_button"):
        bank = chr(ord("A") + gi // PADS_PER_BANK)
        window.map_selection_button.setText(f"Assign to pad {bank}{gi % PADS_PER_BANK + 1}")
    if hasattr(window, "synth_panel"):
        window.synth_panel.sync_target_pad()
    window.pads.update()


def print_synth_to_pad(window):
    """Render the current analog patch and place it on the active pad."""
    patch = window.project.synth
    note = window.synth_panel.base_note
    hold = (60.0 / window.project.bpm) * 2.0
    window.status.showMessage(f"printing {patch.name}…")
    QApplication.processEvents()
    audio = render_patch(patch, note, hold, window.engine.sr)
    window.snapshot()
    clip = window.library.add_audio(
        audio, f"{patch.name} C{window.synth_panel.octave}", kind="render"
    )
    gi = window.pads.selected
    map_sample_range(window.project.pads[gi], clip.id, 0.0, clip.duration, clip.name)
    window.pad_inspector.set_pad(gi)
    window.browser.refresh(select=clip.id)
    window._library_changed()
    window.step_grid.update()
    bank = chr(ord("A") + gi // PADS_PER_BANK)
    window.status.showMessage(f"printed {patch.name} → pad {bank}{gi % PADS_PER_BANK + 1}", 4000)


def _pad_pressed(window, gi: int, vel: float):
    pad = window.project.pads[gi]
    if not pad.empty:
        window.track_capture.note_on(pad.root_note, vel, gi)
    window.engine.trigger_pad(gi, vel)


def _pad_released(window, gi: int):
    window.track_capture.note_off(window.project.pads[gi].root_note, gi)
    window.engine.release_pad(gi)


def _pad_params_changed(window):
    pad = window.project.pads[window.pads.selected]
    if pad.sample_id:
        window.library.audio(pad.sample_id)
        if pad.reverse:
            window.library.reversed_audio(pad.sample_id)
    window.pads.update()
    window.step_grid.update()
    window.mixer.sync()
    window._set_dirty(True)


def normalize_pad(window, gi: int):
    """Set a pad slice peak to -1 dBFS with non-destructive pad gain."""
    pad = window.project.pads[gi]
    data = window.library.audio(pad.sample_id) if pad.sample_id else None
    if data is None or not len(data):
        window.status.showMessage("pad source is missing", 2500)
        return
    s0 = max(0, min(len(data), int(pad.start * window.engine.sr)))
    end = pad.end if pad.end > pad.start else len(data) / window.engine.sr
    s1 = max(s0, min(len(data), int(end * window.engine.sr)))
    peak = float(np.max(np.abs(data[s0:s1]))) if s1 > s0 else 0.0
    if peak <= 1e-7:
        window.status.showMessage("cannot normalize a silent slice", 2500)
        return
    target = 10.0 ** (-1.0 / 20.0)
    gain = min(4.0, target / peak)
    window.snapshot()
    pad.gain = gain
    window.pad_inspector.set_pad(gi)
    window.pads.update()
    capped = " · +12 dB cap" if gain >= 4.0 and peak * gain < target else ""
    window.status.showMessage(f"pad normalized · {20 * np.log10(gain):+.1f} dB{capped}", 3500)


def tighten_pad(window, gi: int):
    """Trim near-silence around a pad while retaining tiny edge padding."""
    pad = window.project.pads[gi]
    data = window.library.audio(pad.sample_id) if pad.sample_id else None
    if data is None or not len(data):
        window.status.showMessage("pad source is missing", 2500)
        return
    sr = window.engine.sr
    s0 = max(0, min(len(data), int(pad.start * sr)))
    end = pad.end if pad.end > pad.start else len(data) / sr
    s1 = max(s0, min(len(data), int(end * sr)))
    segment = data[s0:s1]
    if not len(segment):
        return
    amplitude = np.max(np.abs(segment), axis=1)
    peak = float(amplitude.max())
    if peak <= 1e-7:
        window.status.showMessage("cannot tighten a silent slice", 2500)
        return
    active = np.flatnonzero(amplitude >= max(1e-6, peak * 0.002))
    if not len(active):
        return
    new_s0 = s0 + max(0, int(active[0]) - int(0.001 * sr))
    new_s1 = s0 + min(len(segment), int(active[-1]) + 1 + int(0.003 * sr))
    if new_s0 == s0 and new_s1 == s1:
        window.status.showMessage("slice is already tight", 2200)
        return
    window.snapshot()
    pad.start = new_s0 / sr
    pad.end = new_s1 / sr
    window.pad_inspector.set_pad(gi)
    window.pads.update()
    window.status.showMessage(
        f"trimmed {(new_s0 - s0) / sr * 1000:.1f} ms front · "
        f"{(s1 - new_s1) / sr * 1000:.1f} ms tail",
        3500,
    )


def clear_pad(window, gi: int):
    window.snapshot()
    window.project.pads[gi] = Pad()
    window.pad_inspector.set_pad(gi)
    window.pads.update()
    window.step_grid.update()


def assign_sample_to_pad(window, gi: int, clip_id: str):
    clip = window.library.clips.get(clip_id)
    if not clip:
        return
    window.snapshot()
    pad = window.project.pads[gi]
    pad.sample_id = clip_id
    pad.name = clip.name
    pad.start = 0.0
    pad.end = 0.0
    pad.sync_beats = 0.0
    window.library.audio(clip_id)  # warm the cache off the audio thread
    window.select_pad(gi)
    window.pads.update()
    window.step_grid.update()
    window.status.showMessage(
        f"{clip.name} → pad {chr(ord('A') + gi // PADS_PER_BANK)}{gi % PADS_PER_BANK + 1}", 2500
    )


def assign_range_to_pad(window, gi: int, clip_id: str, start: float, end: float):
    """A range dragged out of the CHOP editor and dropped on a pad."""
    clip = window.library.clips.get(clip_id)
    if not clip or end <= start:
        return
    window.snapshot()
    window.library.audio(clip_id)
    kinds = window._scan_kinds.get(clip_id, {})
    index = window.wave.slice_at(start) if clip_id == window.current_clip else -1
    kind = kinds.get(index, "cut")
    map_sample_range(window.project.pads[gi], clip_id, start, end, f"{kind} {clip.name[:8]}")
    window.select_pad(gi)
    window.pads.update()
    window.step_grid.update()
    bank = chr(ord("A") + gi // PADS_PER_BANK)
    window.status.showMessage(
        f"{start:.3f}s → {end:.3f}s dropped on pad {bank}{gi % PADS_PER_BANK + 1}", 4000
    )


def _set_sample_cut_mode(window, enabled):
    window.wave.cut_mode = enabled
    window.wave.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)


def send_selection_to_arrangement(window):
    if not window.current_clip:
        window.status.showMessage("Select a sample first", 2500)
        return None
    return window.append_sample_to_arrangement(window.current_clip, *window.wave.selection())


def append_sample_to_arrangement(window, ref, start, end):
    meta = window.library.clips.get(ref)
    if meta is None or not 0 <= start < end <= meta.duration + 1e-6:
        return None
    selected_row = window.playlist.row_for_clip(window.playlist.selected_clip)
    rows = window.project.rows
    row_index = next(
        (i for i, row in enumerate(rows) if row is selected_row),
        next((i for i, row in enumerate(rows) if not row.clips), len(rows)),
    )
    window.snapshot()
    if row_index == len(rows):
        rows.append(Row(name=f"TRACK {row_index + 1}"))
    tail = max((c.start_beat + c.length_beats for c in rows[row_index].clips), default=0.0)
    snap = window.playlist.snap
    beat = float(np.ceil((tail - 1e-9) / snap) * snap) if snap else tail
    clip = window.playlist.place_sample_range(
        row_index, max(0.0, beat), ref, start, end, snapshot=False
    )
    window._arrange_drop_filter.reveal()
    window.song_scroll.ensureVisible(
        int(window.playlist.beat_to_x(clip.start_beat) + 20),
        int(RULER_H + (row_index + 0.5) * ROW_H),
        40,
        ROW_H,
    )
    return clip


def load_clip_into_editor(window, clip_id: str):
    window._syncing_zoom = False
    clip = window.library.clips.get(clip_id)
    if not clip:
        return
    window.current_clip = clip_id
    audio = window.library.audio(clip_id)
    peaks = window.library.peaks(clip_id)
    markers = window.project.slices.get(clip_id, [])
    for box in (window.selection_start, window.selection_end):
        box.blockSignals(True)
        box.setRange(0.0, clip.duration)
        box.blockSignals(False)
    window.wave.set_clip(audio, peaks, clip.duration, markers, clip.bpm, clip_id=clip_id)
    window._apply_scan_to_wave(clip_id)
    window.nav.set_overview(window.library.overview(clip_id))
    label = f"{clip.name}   ·   {clip.duration:.2f}s"
    if clip.bpm:
        label += f"   ·   {clip.bpm} BPM"
    window.clip_label.setText(label)
    window.phrase_bpm.setValue(clip.bpm or window.project.bpm)
    window._rebuild_chips()


def edit_sample(window, clip_id: str):
    window.browser.refresh(select=clip_id)
    window.load_clip_into_editor(clip_id)
    gi = window.pads.selected
    pad = window.project.pads[gi]
    if pad.sample_id == clip_id:
        clip = window.library.clips[clip_id]
        window.wave.set_selection(pad.start, pad.end or clip.duration, ensure_visible=True)
    if window.tabs.currentIndex() == 8:
        window.studio.select(0)
    else:
        window.tabs.setCurrentIndex(0)


def _span_to_slider(window, span: float) -> int:
    span = min(1.0, max(window.ZOOM_MIN_SPAN, span))
    return int(round(1000 * np.log(span) / np.log(window.ZOOM_MIN_SPAN)))


def _slider_to_span(window, ticks: int) -> float:
    return float(window.ZOOM_MIN_SPAN ** (ticks / 1000.0))


def _wave_zoom_slider(window, ticks: int):
    if window._syncing_zoom or window.wave.duration <= 0:
        return
    span = window.wave.view_b - window.wave.view_a
    want = window._slider_to_span(ticks)
    # Zoom about the selection when there is one, so trimming a chop keeps
    # the chop on screen instead of drifting off the edge.
    start, end = window.wave.selection()
    focus = (start + end) / 2 / window.wave.duration if end > start else None
    window.wave.zoom_by(want / max(1e-9, span), focus)


def _wave_view_changed(window):
    span = window.wave.view_b - window.wave.view_a
    window._syncing_zoom = True
    window.wave_zoom.setValue(window._span_to_slider(span))
    window._syncing_zoom = False
    seconds = span * window.wave.duration
    if window.wave.duration <= 0:
        window.zoom_readout.setText("—")
    elif seconds < 1.0:
        window.zoom_readout.setText(f"{seconds * 1000:.0f} ms visible")
    else:
        window.zoom_readout.setText(f"{seconds:.2f} s visible")
    window.nav.update()


def _selection_changed(window, start: float, end: float):
    window.selection_start.blockSignals(True)
    window.selection_end.blockSignals(True)
    window.selection_start.setValue(start)
    window.selection_end.setValue(end)
    window.selection_start.blockSignals(False)
    window.selection_end.blockSignals(False)
    window.selection_length.setText(f"{max(0.0, end - start):.3f} s")


def _selection_spin_changed(window, _value: float):
    if not window.current_clip:
        return
    start = window.selection_start.value()
    end = window.selection_end.value()
    if end <= start:
        sender = window.sender()
        if sender is window.selection_start:
            end = min(window.wave.duration, start + 0.001)
        else:
            start = max(0.0, end - 0.001)
    window.wave.set_selection(start, end, snap=True)


def _selection_finished(window, start: float, end: float):
    if window.current_clip and end > start:
        window.engine.audition(
            window.current_clip, start, end, loop=window.btn_loop_range.isChecked()
        )
        window.status.showMessage(f"range {start:.3f}s → {end:.3f}s · {(end - start):.3f}s", 2500)


def audition_selection(window):
    if not window.current_clip:
        window.status.showMessage("select a sample first", 2500)
        return
    start, end = window.wave.selection()
    window.engine.audition(window.current_clip, start, end, loop=window.btn_loop_range.isChecked())


def _loop_range_toggled(window, on: bool):
    if on:
        window.audition_selection()
    else:
        window.engine.stop_audition()


def _scrubbed(window, seconds: float):
    """Clicking the ruler plays from there to the end of the range."""
    if not window.current_clip:
        return
    end = max(seconds + 0.05, window.wave.selection_end)
    window.engine.audition(
        window.current_clip,
        seconds,
        min(end, window.wave.duration),
        loop=window.btn_loop_range.isChecked(),
    )


def _wave_menu(window, position, seconds: float):
    if not window.current_clip:
        return
    menu = QMenu(window)
    start, end = window.wave.selection()
    index = window.wave.slice_at(seconds)

    act_play = menu.addAction("Play from here")
    act_range = menu.addAction("Play range")
    menu.addSeparator()
    act_mark = menu.addAction("Split here")
    act_select = menu.addAction("Select this slice") if index >= 0 else None
    act_bar = menu.addAction("Select one bar from here") if window.wave.bpm else None
    act_all = menu.addAction("Select whole sample")
    menu.addSeparator()
    gi = window.pads.selected
    bank = chr(ord("A") + gi // PADS_PER_BANK)
    act_map = menu.addAction(f"Map range → pad {bank}{gi % PADS_PER_BANK + 1}")
    act_new = menu.addAction("Save range as a new sample")
    act_arrange = menu.addAction("Send range to Arrange")
    menu.addSeparator()
    act_zoom = menu.addAction("Zoom to range")
    act_fit = menu.addAction("Fit whole sample")

    chosen = menu.exec(position)
    if chosen is None:
        return
    if chosen is act_play:
        window._scrubbed(seconds)
    elif chosen is act_range:
        window.audition_selection()
    elif chosen is act_mark:
        window.wave.add_marker(seconds)
    elif act_select is not None and chosen is act_select:
        window.wave.select_slice(index)
    elif act_bar is not None and chosen is act_bar:
        bar = (60.0 / window.wave.bpm) * 4
        window.wave.set_selection(seconds, min(window.wave.duration, seconds + bar))
        window.audition_selection()
    elif chosen is act_all:
        window.wave.set_selection(0.0, window.wave.duration)
    elif chosen is act_map:
        window.map_selection_to_pad()
    elif chosen is act_new:
        window.save_range_as_sample(start, end)
    elif chosen is act_arrange:
        window.send_selection_to_arrangement()
    elif chosen is act_zoom:
        window.wave.zoom_to_selection()
    elif chosen is act_fit:
        window.wave.fit()


def save_range_as_sample(window, start: float, end: float):
    """Bounce the chosen range into the library as a sample of its own."""
    audio = window.library.audio(window.current_clip)
    if audio is None or end <= start:
        return
    sr = window.engine.sr
    cut = audio[int(start * sr) : int(end * sr)]
    if not len(cut):
        window.status.showMessage("range is empty", 2500)
        return
    source = window.library.clips[window.current_clip]
    window.snapshot()
    clip = window.library.add_audio(cut, f"{source.name[:20]} cut", kind="chop")
    window.browser.refresh(select=clip.id)
    window._library_changed()
    window.status.showMessage(f"{clip.name} · {clip.duration:.3f}s → library", 5000)


def map_selection_to_pad(window):
    window._map_selection_to_pad()


def _map_selection_to_pad(window) -> bool:
    if not window.current_clip:
        window.status.showMessage("select a sample first", 2500)
        return False
    start, end = window.wave.selection()
    if end - start < 0.001:
        window.status.showMessage("drag a range on the waveform first", 2500)
        return False
    gi = window.pads.selected
    clip = window.library.clips[window.current_clip]
    window.library.audio(window.current_clip)
    window.snapshot()
    pad = window.project.pads[gi]
    map_sample_range(pad, window.current_clip, start, end, f"{clip.name[:12]} cut")
    window.pad_inspector.set_pad(gi)
    window.pads.update()
    window.step_grid.update()
    bank = chr(ord("A") + gi // PADS_PER_BANK)
    window.status.showMessage(
        f"{start:.3f}s → {end:.3f}s mapped to pad {bank}{gi % PADS_PER_BANK + 1}", 4000
    )
    return True


def map_selection_and_next(window):
    start, end = window.wave.selection()
    if not window._map_selection_to_pad():
        return
    gi = (window.pads.selected + 1) % NPADS
    bank = gi // PADS_PER_BANK
    if bank != window.pads.bank:
        window.set_bank(bank)
    window.select_pad(gi)

    length = end - start
    next_start = end
    next_end = min(window.wave.duration, next_start + length)
    if next_end - next_start < 0.001:
        next_start = max(0.0, window.wave.duration - length)
        next_end = window.wave.duration
    window.wave.set_selection(next_start, next_end, ensure_visible=True, snap=True)


def _chop_mode_changed(window, idx):
    transient = idx == 0
    window.sens.setVisible(transient)
    window.sens_label.setVisible(transient)
    window.pieces.setVisible(not transient)
    window.pieces_label.setVisible(not transient)
    window.btn_scan.setText("Find slices" if transient else "Slice sample")
    window.btn_scan.setToolTip(
        "Detect cuts throughout the sample using the sensitivity setting"
        if transient
        else "Divide the sample using the selected grid and piece count"
    )


def select_four_bar_phrase(window):
    if not window.current_clip:
        window.status.showMessage("Select a sample first", 2500)
        return
    clip = window.library.clips[window.current_clip]
    start = window.wave.selection_start
    end = start + 16 * 60 / window.phrase_bpm.value()
    if end > clip.duration + 0.5 / window.engine.sr:
        window.status.showMessage("Not enough audio for four bars from this start", 4000)
        return
    window.wave.set_selection(start, min(end, clip.duration), ensure_visible=True)
    window.status.showMessage(
        "Four bars selected · preview and refine the range before chopping", 4500
    )


def arrange_four_bar_phrase(window):
    if not window.current_clip:
        window.status.showMessage("Select a sample and its four-bar phrase first", 3500)
        return
    source = window.library.clips[window.current_clip]
    audio = window.library.audio(source.id)
    if audio is None:
        window.status.showMessage("Sample audio is unavailable", 3500)
        return
    try:
        plan = four_bar_phrase(
            window.project,
            source.id,
            source.name,
            window.wave.selection_start,
            window.wave.selection_end,
            len(audio),
            window.engine.sr,
            window.phrase_pieces.currentData(),
            window.pads.bank,
        )
    except ValueError as exc:
        window.status.showMessage(str(exc), 5000)
        return
    row_index, start = pattern_arrangement_target(
        window.project, plan.pattern, window.playlist.selected_clip, 4.0
    )
    window.snapshot()
    for index, pad in plan.pads:
        window.project.pads[index] = pad
    window.project.patterns.append(plan.pattern)
    window.project.current_pattern = plan.pattern.id
    if row_index == len(window.project.rows):
        window.project.rows.append(Row(name=f"TRACK {row_index + 1}"))
    clip = window.playlist._place_ref(row_index, start, "pattern", plan.pattern.id)
    window._sync_pattern_controls()
    window._refresh_place_box()
    window._choose_pattern_to_place(plan.pattern.id)
    window.set_bank(plan.bank)
    window.select_pad(plan.pads[0][0])
    window.playlist.select_clip(clip)
    window.phrase_bpm.setValue(plan.source_bpm)
    if window.studio.enabled:
        window.studio.select(window.TAB_PLAYLIST)
    else:
        window.show_tab(window.TAB_PLAYLIST)
    window.song_scroll.ensureVisible(
        int(window.playlist.beat_to_x(start) + 20),
        int(RULER_H + (row_index + 0.5) * ROW_H),
        40,
        ROW_H,
    )
    window.status.showMessage(
        f"{len(plan.pads)} chops · bank {chr(65 + plan.bank)} · 4 bars at song tempo "
        f"(repitch) · double-click to rearrange · Ctrl+Z to undo",
        8000,
    )
    return clip
