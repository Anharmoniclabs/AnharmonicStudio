"""Window transport.

Functions receive the workstation coordinator explicitly; Qt ownership and
project state stay with that coordinator. This module owns only its named domain.
"""

from __future__ import annotations
import time
import numpy as np
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QSpinBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
)
from ..music import Note


def _record_toggled(window, enabled):
    window._record_count_timer.stop()
    window._record_count_deadline = None
    window.record_count_label.hide()
    capture = window.track_capture
    if enabled and capture.armed_id is None and window.engine.mode == "song":
        window.btn_rec.blockSignals(True)
        window.btn_rec.setChecked(False)
        window.btn_rec.blockSignals(False)
        window.track_controls_button.setChecked(True)
        window.show_tab(2)
        window.status.showMessage("Arm a Song track with its R button, then press Record", 5000)
        return
    if not enabled and (capture.active or capture.pending):
        capture.finish()
        return
    if enabled and capture.armed_id is not None:
        if not capture.prepare():
            window.btn_rec.blockSignals(True)
            window.btn_rec.setChecked(False)
            window.btn_rec.blockSignals(False)
            return
        window.show_tab(2)
        window.engine.recording = False
        window.engine.stop_transport(rewind=False)
        window.engine.mode = "song"
        window.btn_pattern.setChecked(False)
        window.btn_song.setChecked(True)
        window._record_count_beat_seconds = 60.0 / max(1.0, window.project.bpm)
        count = capture.settings.count_in_bars * 4
        if count == 0:
            capture.start()
            return
        window._record_count_deadline = time.monotonic() + count * window._record_count_beat_seconds
        window.record_count_label.setText(str(count))
        window.record_count_label.show()
        window._record_count_timer.start()
        window.status.showMessage(f"Count-in · {capture.target.name} · Stop cancels")
        return
    if not enabled:
        for note in tuple(window.sample_workflow.recorded):
            window.sample_workflow.note_off(note)
        for note in list(window._recorded_notes):
            window.release_synth_note(note)
        window.engine.recording = False
        return
    window.engine.recording = False
    window.engine.stop_transport(rewind=False)
    window._record_count_beat_seconds = 60.0 / max(1.0, window.project.bpm)
    window._record_count_deadline = time.monotonic() + 3 * window._record_count_beat_seconds
    window.record_count_label.setText("3")
    window.record_count_label.show()
    window.status.showMessage("Count-in · 3 · Record or Stop cancels")
    window._record_count_timer.start()


def _advance_record_count(window):
    if window._record_count_deadline is None:
        return
    remaining = window._record_count_deadline - time.monotonic()
    if remaining > 0:
        import math

        count = max(1, math.ceil(remaining / window._record_count_beat_seconds))
        window.record_count_label.setText(str(count))
        return
    window._record_count_timer.stop()
    window._record_count_deadline = None
    window.record_count_label.hide()
    if window.track_capture.pending:
        window.track_capture.start()
        return
    window.snapshot()
    window.engine.recording = True
    window.engine.play()
    window.status.showMessage("Recording", 2500)


def _cancel_record_count(window):
    if window._record_count_deadline is not None:
        window.btn_rec.setChecked(False)


def toggle_play(window):
    if window.track_capture.active:
        window.btn_rec.setChecked(False)
        return
    if window._record_count_deadline is not None:
        window._cancel_record_count()
        return
    window.engine.toggle_play()


def play_selected_note(window, note: int, velocity: float = 1.0):
    window.sample_workflow.note_on(note, velocity)


def release_selected_note(window, note: int):
    window.sample_workflow.note_off(note)


def play_synth_note(window, note: int, velocity: float = 1.0):
    if not window.project.arp.enabled:
        window.track_capture.note_on(note, velocity)
    if (
        not window.project.arp.enabled
        and window.engine.recording
        and window.engine.playing
        and window.engine.mode == "pattern"
    ):
        if note not in window._recorded_notes:
            window.snapshot()
            window._recorded_notes[note] = (
                window.project.pattern().id,
                window.engine.beat,
                velocity,
            )
    window.engine.synth_note_on(note, velocity)
    window.synth_panel.keyboard.set_note_active(note, True)
    if window.typing_keyboard is not None:
        window.typing_keyboard.keyboard.set_note_active(note, True)


def release_synth_note(window, note: int):
    window.track_capture.note_off(note)
    recorded = window._recorded_notes.pop(note, None)
    if recorded:
        pattern_id, start, velocity = recorded
        pattern = next((p for p in window.project.patterns if p.id == pattern_id), None)
        if pattern:
            beat = start % pattern.length_beats
            duration = min(max(0.03125, window.engine.beat - start), pattern.length_beats - beat)
            pattern.notes.append(Note(note, beat, duration, velocity))
            window._set_dirty(True)
            window.piano_roll.canvas.refresh()
    window.engine.synth_note_off(note)
    window.synth_panel.keyboard.set_note_active(note, False)
    if window.typing_keyboard is not None:
        window.typing_keyboard.keyboard.set_note_active(note, False)


def panic_synth(window):
    window.sample_workflow.panic()
    for note in list(window._recorded_notes):
        window.release_synth_note(note)
    window.engine.synth_panic()
    window._held_synth_keys.clear()
    window.synth_panel.keyboard.active.clear()
    window.synth_panel.keyboard.update()
    if window.typing_keyboard is not None:
        window.typing_keyboard.panic(send=False)


def stop_all(window):
    if window.track_capture.active or window.track_capture.pending:
        window.btn_rec.setChecked(False)
    window.sample_workflow.panic()
    window._cancel_record_count()
    for note in list(window._recorded_notes):
        window.release_synth_note(note)
    window.engine.stop_transport(rewind=True)
    window.engine.panic()
    window._held_pads.clear()
    window._held_synth_keys.clear()
    window.synth_panel.keyboard.active.clear()
    window.synth_panel.keyboard.update()
    if window.typing_keyboard is not None:
        window.typing_keyboard.panic(send=False)


def set_mode(window, mode: str):
    if window.track_capture.active or window.track_capture.pending:
        if mode == window.engine.mode:
            return
        window.btn_rec.setChecked(False)
    window.sample_workflow.panic()
    window._cancel_record_count()
    for note in list(window._recorded_notes):
        window.release_synth_note(note)
    window.engine.mode = mode
    window.btn_pattern.setChecked(mode == "pattern")
    window.btn_song.setChecked(mode == "song")
    window.engine.stop_transport(rewind=True)
    if mode == "song":
        window.tabs.setCurrentIndex(2)


def _song_loop_toggled(window, on: bool):
    capture = getattr(window, "track_capture", None)
    if capture is not None and capture.busy:
        window.btn_song_loop.blockSignals(True)
        window.btn_song_loop.setChecked(window.project.loop_enabled)
        window.btn_song_loop.blockSignals(False)
        window.status.showMessage("Stop and save the take before changing the loop", 3000)
        return
    window.engine.loop_song = bool(on)
    window.project.loop_enabled = bool(on)
    window._set_dirty(True)
    if on:
        window.set_mode("song")
        if not (window.project.loop_start <= window.engine.beat < window.project.loop_end):
            window.engine.set_position(window.project.loop_start)
    window.playlist.update()
    window.status.showMessage(
        f"song loop {'on' if on else 'off'} · "
        f"{window.project.loop_start:g}–{window.project.loop_end:g} beats",
        2600,
    )


def _seek_song(window, beat):
    if window.track_capture.active or window.track_capture.pending:
        window.status.showMessage("Stop the take before moving the playhead", 3000)
        return
    window.engine.set_position(beat)


def set_song_loop_range(window, start: float, end: float, enable: bool = False):
    start = max(0.0, float(start))
    end = max(start + 0.25, float(end))
    window.project.loop_start, window.project.loop_end = start, end
    for box, value in ((window.loop_start_box, start), (window.loop_end_box, end)):
        box.blockSignals(True)
        box.setValue(value)
        box.blockSignals(False)
    bars = max(1, round((end - start) / 4.0))
    window.playlist_loop_bars.blockSignals(True)
    window.playlist_loop_bars.setValue(bars)
    window.playlist_loop_bars.blockSignals(False)
    if enable:
        window.btn_song_loop.setChecked(True)
    window._update_loop_button()
    window.playlist.update()


def _loop_boxes_changed(window):
    start = window.loop_start_box.value()
    end = max(start + 0.25, window.loop_end_box.value())
    if end != window.loop_end_box.value():
        window.loop_end_box.blockSignals(True)
        window.loop_end_box.setValue(end)
        window.loop_end_box.blockSignals(False)
    window.project.loop_start, window.project.loop_end = start, end
    bars = max(1, round((end - start) / 4.0))
    window.playlist_loop_bars.blockSignals(True)
    window.playlist_loop_bars.setValue(bars)
    window.playlist_loop_bars.blockSignals(False)
    window._update_loop_button()
    window.playlist.update()


def _playlist_loop_bars_changed(window, bars: int):
    """Set loop duration from the Playlist, measured in four-beat bars."""
    start = window.loop_start_box.value()
    window.set_song_loop_range(start, start + int(bars) * 4.0)


def _update_loop_button(window):
    if not hasattr(window, "btn_loop_setup"):
        return
    start_bar = int(window.project.loop_start // 4) + 1
    bars = max(1, round((window.project.loop_end - window.project.loop_start) / 4))
    window.btn_loop_setup.setText(f"BAR {start_bar} · {bars} BARS")


def edit_playlist_loop(window):
    dlg = QDialog(window)
    dlg.setWindowTitle("Playlist loop")
    form = QFormLayout(dlg)
    start = QDoubleSpinBox()
    start.setRange(0, 100000)
    start.setDecimals(2)
    start.setSuffix(" beats")
    start.setValue(window.project.loop_start)
    bars = QSpinBox()
    bars.setRange(1, 256)
    bars.setValue(max(1, round((window.project.loop_end - window.project.loop_start) / 4)))
    form.addRow("Start", start)
    form.addRow("Length", bars)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    form.addRow(buttons)
    if dlg.exec() == QDialog.Accepted:
        window.snapshot()
        window.set_song_loop_range(start.value(), start.value() + bars.value() * 4, enable=True)


def tap_tempo(window):
    now = time.monotonic()
    window._taps = [t for t in window._taps if now - t < 2.4] + [now]
    if len(window._taps) < 2:
        window.status.showMessage("tap…", 1500)
        return
    gaps = np.diff(window._taps)
    bpm = float(np.clip(60.0 / gaps.mean(), 40, 240))
    window.project.bpm = round(bpm, 2)
    window.bpm_box.setValue(window.project.bpm)
    window.status.showMessage(f"{window.project.bpm} BPM", 1800)


def _bpm_changed(window, value: float):
    capture = getattr(window, "track_capture", None)
    if capture is not None and capture.busy:
        window.bpm_box.blockSignals(True)
        window.bpm_box.setValue(window.project.bpm)
        window.bpm_box.blockSignals(False)
        window.status.showMessage("Stop and save the take before changing tempo", 3000)
        return
    window.project.bpm = float(value)
    window._set_dirty(True)


def _swing_changed(window, v):
    window.project.swing = float(v)
    window.swing_label.setText(f"{v}%")
    window._set_dirty(True)


def _cut_self_changed(window, on: bool):
    window.project.self_choke = bool(on)
    window._set_dirty(True)
    window.status.showMessage(
        "cut source on — cuts stay within each pattern or live performance; layers keep playing"
        if on
        else "cut source off — same-sample slices may overlap",
        1800,
    )


def _master_changed(window, v):
    window.project.master = v / 100.0
    window.mixer.master_strip.sync()
    window._set_dirty(True)


def _audio_buffer_changed(window, index: int):
    frames = int(window.audio_buffer.itemData(index) or window.engine.blocksize)
    if frames == window.engine.blocksize:
        return
    old = window.engine.blocksize
    window.status.showMessage(
        f"restarting audio · {frames} frames / {frames / window.engine.sr * 1000:.1f} ms"
    )
    try:
        if window.engine.stream is None:
            window.engine.configure_blocksize(frames)
            window.engine.start()
        else:
            window.engine.restart(frames)
    except Exception as exc:
        # ``restart`` rolls a running stream back. If an offline retry
        # failed, restore the previous prepared size as well.
        if window.engine.stream is None and window.engine.blocksize != old:
            window.engine.configure_blocksize(old)
        previous = window.audio_buffer.findData(old)
        window.audio_buffer.blockSignals(True)
        window.audio_buffer.setCurrentIndex(max(0, previous))
        window.audio_buffer.blockSignals(False)
        error = str(exc) or type(exc).__name__
        if window.engine.stream is not None:
            # Engine.restart() was able to reopen the previous profile.
            # The requested period failed, but audio itself is still live.
            window._audio_start_error = None
            window.status.showMessage(
                f"audio profile failed · restored {old} frames · {error}", 8000
            )
        else:
            window._audio_start_error = error
            window.status.showMessage(f"audio offline · {window._audio_start_error}", 8000)
        return
    window._audio_start_error = None
    window.settings.setValue("audio/buffer_frames", frames)
    if hasattr(window, "devices") and window.project.plugins:
        window.devices.sync_project()
    window.status.showMessage(
        f"audio · {frames} frames · {window.engine.period_ms:.1f} ms block", 4000
    )


def _retry_audio(window):
    if window.engine.stream is not None:
        window.engine.reset_timing()
        window._last_audio_warning_xruns = 0
        window._last_audio_warning_at = 0.0
        window.status.showMessage("audio diagnostics reset", 2500)
        return
    try:
        window.engine.start()
    except Exception as exc:
        window._audio_start_error = str(exc) or type(exc).__name__
        window.status.showMessage(f"audio still offline · {window._audio_start_error}", 8000)
        return
    window._audio_start_error = None
    window.status.showMessage(f"audio online · {window.engine.blocksize} frames", 4000)
