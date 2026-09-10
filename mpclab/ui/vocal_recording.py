"""Vocal recording.

The caller retains Qt/project ownership; these operations receive it explicitly.
"""

from __future__ import annotations
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QMessageBox,
)
from ..vocal import (
    input_device_inventory,
)


def _record_settings_changed(owner, *_):
    if owner._syncing:
        return
    owner.app.snapshot()
    rec = owner.app.project.vocal_record
    rec.input_device = str(owner.input_box.currentData() or "")
    rec.input_gain_db = owner.input_gain.value()
    rec.input_latency_ms = owner.input_latency.value()
    rec.monitor = owner.monitor.isChecked()
    rec.monitor_gain = owner.monitor_gain.value() / 100.0
    rec.count_in_bars = int(owner.count_in.currentData() or 0)
    rec.auto_place = owner.auto_place.isChecked()
    rec.playlist_row = owner.row_box.value() - 1
    rec.mixer_track = int(owner.track_box.currentData() or 0)
    owner.app._set_dirty(True)


def _tune_settings_changed(owner, *_):
    if owner._syncing:
        return
    if not owner._batch_tune_edit:
        owner.app.snapshot()
    tune = owner.app.project.vocal
    tune.enabled = owner.autotune_enabled.isChecked()
    tune.key = owner.key_box.currentText()
    tune.scale = owner.scale_box.currentText()
    tune.low_note, tune.high_note = owner.range_box.currentData() or (36, 84)
    tune.strength = owner.strength.value() / 100.0
    tune.retune_ms = owner.retune.value()
    tune.humanize = owner.humanize.value() / 100.0
    tune.mix = owner.mix.value() / 100.0
    tune.formant = owner.formant.value() / 100.0
    tune.transpose = int(owner.transpose.value())
    tune.gate_db = owner.gate.value()
    tune.highpass_hz = owner.highpass.value()
    tune.deesser = owner.deesser.value() / 100.0
    tune.compression = owner.compression.value() / 100.0
    tune.presence_db = owner.presence.value()
    tune.output_db = owner.output.value()
    owner.pitch_view.set_settings(tune)
    owner._sync_root_keys()
    owner.app._set_dirty(True)


def _sync_root_keys(owner):
    for root, button in owner.root_keys.items():
        button.setChecked(root == owner.key_box.currentText())


def _apply_preset(owner, name: str):
    presets = {
        "Natural vocal": (75, 80, 45, 100, 90, 25, 30, 1.5),
        "Modern vocal": (95, 22, 15, 100, 70, 38, 55, 2.5),
        "Hard tune": (100, 0, 0, 100, 35, 45, 70, 3.5),
        "Rap lead": (92, 12, 8, 100, 55, 55, 65, 3.0),
    }
    values = presets.get(name)
    if values is None:
        return
    if not owner._syncing:
        owner.app.snapshot()
    owner._batch_tune_edit = True
    try:
        for widget, value in zip(
            (
                owner.strength,
                owner.retune,
                owner.humanize,
                owner.mix,
                owner.formant,
                owner.deesser,
                owner.compression,
                owner.presence,
            ),
            values,
            strict=True,
        ):
            widget.setValue(value)
        owner._tune_settings_changed()
    finally:
        owner._batch_tune_edit = False


def scan_inputs(owner):
    try:
        inputs, default = input_device_inventory()
    except Exception as exc:
        owner.record_status.setText(f"input scan failed · {exc}")
        return
    wanted = owner.app.project.vocal_record.input_device
    owner._inputs = inputs
    owner.input_box.blockSignals(True)
    owner.input_box.clear()
    default_name = next(
        (item["name"] for item in inputs if item["index"] == default), "system default"
    )
    owner.input_box.addItem(f"System default · {default_name}", "")
    for item in inputs:
        owner.input_box.addItem(item["label"], item["key"])
    index = owner.input_box.findData(wanted)
    owner.input_box.setCurrentIndex(max(0, index))
    owner.input_box.blockSignals(False)
    owner.record_status.setText(f"{len(inputs)} microphone input(s) available")


def toggle_recording(owner):
    capture = getattr(owner.app, "track_capture", None)
    if capture is not None and capture.busy:
        owner.record_status.setText("Stop and save the Song track take first")
        return
    if owner.recorder.recording or owner.recorder.temporary_path is not None:
        owner.stop_recording()
        return
    if owner._counting:
        owner._countdown_token += 1
        owner._counting = False
        owner._restore_count_in_transport()
        owner.record_button.setText("●  START VOCAL TAKE")
        owner.record_status.setText("count-in cancelled")
        return
    owner._countdown_token += 1
    bars = int(owner.count_in.currentData() or 0)
    if bars:
        token = owner._countdown_token
        seconds = bars * 4.0 * 60.0 / owner.app.project.bpm
        owner._count_in_transport = (
            bool(owner.app.engine.playing),
            bool(owner.app.engine.metronome),
        )
        owner.app.engine.metronome = True
        owner.app.btn_metro.setChecked(True)
        if not owner.app.engine.playing:
            owner.app.engine.play()
        owner._counting = True
        owner.record_button.setText(
            f"CANCEL · COUNTING {bars} BAR" + ("S" if bars > 1 else "") + "…"
        )
        owner.record_status.setText("count-in running · recording starts after the downbeat")
        QTimer.singleShot(int(seconds * 1000), owner, lambda: owner._start_after_count(token))
    else:
        owner._start_capture()


def _restore_count_in_transport(owner):
    """Undo only transport changes made for an interrupted count-in."""
    previous, owner._count_in_transport = owner._count_in_transport, None
    if previous is None:
        return
    was_playing, had_metronome = previous
    owner.app.engine.metronome = had_metronome
    owner.app.btn_metro.setChecked(had_metronome)
    if not was_playing:
        # Stop even when the engine has not consumed the queued play yet;
        # command ordering guarantees the count-in play cannot leak later.
        owner.app.engine.stop_transport(False)
    elif not owner.app.engine.playing:
        owner.app.engine.play()


def _start_after_count(owner, token: int):
    if token != owner._countdown_token:
        return
    owner._counting = False
    owner._start_capture()


def _start_capture(owner):
    rec = owner.app.project.vocal_record
    selected = next((item for item in owner._inputs if item["key"] == rec.input_device), None)
    device = selected["index"] if selected is not None else None
    monitor_callback = (
        (lambda block: owner.app.engine.queue_monitor(block, rec.monitor_gain))
        if rec.monitor
        else None
    )
    try:
        owner.recorder.start(device, rec.input_gain_db, monitor_callback)
    except Exception as exc:
        owner._restore_count_in_transport()
        owner.record_button.setEnabled(True)
        owner.record_button.setText("●  START VOCAL TAKE")
        owner.record_status.setText(f"recording failed · {exc}")
        QMessageBox.warning(owner, "Vocal input failed", str(exc))
        return
    # Successful capture owns the transport state from here onward.
    owner._count_in_transport = None
    owner._record_start_beat = float(owner.app.engine.beat)
    owner.record_button.setText("■  STOP + SAVE TAKE")
    owner.pause_button.setEnabled(True)
    owner.discard_button.setEnabled(True)
    owner.record_status.setText("recording 24-bit library take · original stays dry")


def _pause_changed(owner, paused: bool):
    owner.recorder.paused = bool(paused)
    owner.pause_button.setText("RESUME" if paused else "PAUSE")


def stop_recording(owner):
    try:
        audio = owner.recorder.stop()
    except Exception as exc:
        owner._finish_record_controls()
        owner.record_status.setText(f"capture failed · {exc}")
        QMessageBox.warning(owner, "Save take failed", str(exc))
        return
    owner._finish_record_controls()
    if len(audio) < int(owner.app.engine.sr * 0.08):
        owner.recorder.discard()
        owner._finish_record_controls()
        owner.record_status.setText("take was too short and was not saved")
        return
    stamp = time.strftime("%Y-%m-%d %H%M%S")
    name = owner.take_name.text().strip() or f"Vocal {stamp}"
    previous_redo = list(getattr(owner.app, "_redo", []))
    was_dirty = getattr(owner.app, "_dirty", False)
    owner.app.snapshot()
    try:
        clip = owner.app.library.add_audio(audio, name, kind="vocal")
    except Exception as exc:
        if hasattr(owner.app, "discard_snapshot"):
            owner.app.discard_snapshot()
            owner.app._redo[:] = previous_redo
            owner.app._set_dirty(was_dirty)
            owner.app._try_save_history()
        owner.record_status.setText(f"Take retained · retry save · {exc}")
        QMessageBox.warning(owner, "Save take failed", str(exc))
        return
    owner.recorder.commit()
    owner._finish_record_controls()
    owner._latest_clip = clip.id
    owner.refresh_takes(select=clip.id)
    if owner.auto_place.isChecked():
        owner._place_clip(clip.id, owner._record_start_beat, compensate_latency=True)
    owner.app._library_changed()
    owner.record_status.setText(
        f"saved {clip.name} · {clip.duration:.1f}s"
        + (f" · {owner.recorder.overruns} input overflow(s)" if owner.recorder.overruns else "")
    )


def discard_recording(owner):
    owner._countdown_token += 1
    owner._restore_count_in_transport()
    try:
        owner.recorder.discard()
    except Exception as exc:
        owner.record_status.setText(f"discard cleanup failed · {exc}")
    owner._finish_record_controls()
    if "failed" not in owner.record_status.text():
        owner.record_status.setText("take discarded · nothing was written")


def _finish_record_controls(owner):
    owner._counting = False
    owner.record_button.setEnabled(True)
    owner.record_button.setText("●  START VOCAL TAKE")
    owner.pause_button.blockSignals(True)
    owner.pause_button.setChecked(False)
    owner.pause_button.blockSignals(False)
    owner.pause_button.setText("PAUSE")
    owner.pause_button.setEnabled(False)
    pending = owner.recorder.temporary_path is not None
    owner.discard_button.setEnabled(pending)
    if pending:
        owner.record_button.setText("RETRY SAVE TAKE")
