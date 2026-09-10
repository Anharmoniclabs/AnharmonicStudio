"""Vocal recording and pitch-correction workspace."""

from __future__ import annotations

import threading

import numpy as np
from .window_client import WindowClient

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QGridLayout,
    QLabel,
    QPushButton,
    QDoubleSpinBox,
    QGroupBox,
    QScrollArea,
    QMessageBox as QMessageBox,
    QInputDialog as QInputDialog,
)

from ..model import Clip, VocalComp, VocalCompRegion
from ..vocal import (
    VocalRecorder,
    analyze_pitch,
)
from .theme import label_font

from . import vocal_layout, vocal_recording, vocal_takes, vocal_comp_actions, vocal_processing


def _small(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    return label


class VocalPanel(WindowClient, QWidget):
    """Offline tuning and comping of existing recordings; capture lives in Song."""

    keyFinished = Signal(int, object, str, str, float)
    keyFailed = Signal(int, str)
    keyProgress = Signal(int, int)
    tuneFinished = Signal(int, object, object, str)
    tuneFailed = Signal(int, str)
    tuneProgress = Signal(int, int)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.recorder = VocalRecorder(
            app.engine.sr, app.engine.blocksize, temp_dir=app.root / "projects" / "recordings"
        )
        self._inputs: list[dict] = []
        self._record_start_beat = 0.0
        self._countdown_token = 0
        self._counting = False
        self._count_in_transport: tuple[bool, bool] | None = None
        self._latest_clip: str | None = None
        self._key_job_id = 0
        self._key_cancel: threading.Event | None = None
        self._tune_job_id = 0
        self._tune_cancel: threading.Event | None = None
        self._render_source_id: str | None = None
        self._render_project = None
        self._key_project = None
        self._key_source_id = None
        self._syncing = True
        self._batch_tune_edit = False
        self._song_clip_id = None
        self._song_source_id = None
        self._build()
        self.keyFinished.connect(self._key_finished)
        self.keyFailed.connect(self._key_failed)
        self.keyProgress.connect(self._key_progress)
        self.tuneFinished.connect(self._tune_finished)
        self.tuneFailed.connect(self._tune_failed)
        self.tuneProgress.connect(self._tune_progress)
        self.meter_timer = QTimer(self)
        self.meter_timer.timeout.connect(self._tick)
        self.meter_timer.start(50)
        self.sync()

    # ── construction ──────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroller = self.workspace_scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 18)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title = QLabel("AUTOTUNE")
        title.setObjectName("title")
        title.setFont(label_font(14, bold=True))
        heading.addWidget(title)
        heading.addStretch()
        self.record_in_song = QPushButton("Record in Song")
        self.record_in_song.setToolTip("Return to the Song timeline to arm a microphone track.")
        self.record_in_song.clicked.connect(lambda: self.app.prepare_vocal_recording())
        heading.addWidget(self.record_in_song)
        layout.addLayout(heading)
        help_text = _small(
            "Choose an existing vocal, shape its tuning, then return the finished take to Song."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        # Keep legacy capture state for recovery and old projects, outside the tuning UI.
        self.legacy_recording = self._record_group()
        self.legacy_recording.setParent(body)
        self.legacy_recording.hide()
        self.edit_tabs = QTabWidget()
        self.edit_tabs.addTab(self._tune_group(), "Pitch editor")
        comp_scroll = QScrollArea()
        comp_scroll.setWidgetResizable(True)
        comp_scroll.setWidget(self._comp_group())
        self.edit_tabs.addTab(comp_scroll, "Take comp")
        layout.addWidget(self.edit_tabs, 1)
        scroller.setWidget(body)
        outer.addWidget(scroller)

    def _record_group(self) -> QGroupBox:
        return vocal_layout._record_group(self)

    def _tune_group(self) -> QWidget:
        return vocal_layout._tune_group(self)

    def _dial(self, row, name, low, high, suffix, tip):
        return vocal_layout._dial(self, row, name, low, high, suffix, tip)

    def _begin_dial_edit(self):
        return vocal_layout._begin_dial_edit(self)

    def _end_dial_edit(self):
        return vocal_layout._end_dial_edit(self)

    def _listening_selection_changed(self, start, end):
        return vocal_layout._listening_selection_changed(self, start, end)

    def open_source(self, clip_id):
        return vocal_layout.open_source(self, clip_id)

    def use_song_selection(self):
        return vocal_layout.use_song_selection(self)

    def open_arranged_take(self, clip):
        return vocal_layout.open_arranged_take(self, clip)

    def apply_to_song(self):
        return vocal_layout.apply_to_song(self)

    def _parameter(
        self, grid: QGridLayout, row: int, name: str, minimum: float, maximum: float, suffix: str
    ) -> QDoubleSpinBox:
        return vocal_layout._parameter(self, grid, row, name, minimum, maximum, suffix)

    def _comp_group(self) -> QGroupBox:
        return vocal_layout._comp_group(self)

    # ── state synchronization ─────────────────────────────
    def sync(self):
        self._syncing = True
        rec = self.app.project.vocal_record
        tune = self.app.project.vocal
        self.input_gain.setValue(rec.input_gain_db)
        self.input_latency.setValue(rec.input_latency_ms)
        self.monitor.setChecked(rec.monitor)
        self.monitor_gain.setValue(rec.monitor_gain * 100.0)
        self.count_in.setCurrentIndex(max(0, self.count_in.findData(rec.count_in_bars)))
        self.auto_place.setChecked(rec.auto_place)
        self.row_box.setValue(rec.playlist_row + 1)
        self.track_box.setCurrentIndex(max(0, self.track_box.findData(rec.mixer_track)))
        self.key_box.setCurrentText(tune.key)
        self.scale_box.setCurrentText(tune.scale)
        self.autotune_enabled.setChecked(tune.enabled)
        wanted_range = (tune.low_note, tune.high_note)
        idx = self.range_box.findData(wanted_range)
        self.range_box.setCurrentIndex(idx if idx >= 0 else self.range_box.count() - 1)
        for widget, value in (
            (self.strength, tune.strength * 100),
            (self.retune, tune.retune_ms),
            (self.humanize, tune.humanize * 100),
            (self.mix, tune.mix * 100),
            (self.formant, tune.formant * 100),
            (self.transpose, tune.transpose),
            (self.gate, tune.gate_db),
            (self.highpass, tune.highpass_hz),
            (self.deesser, tune.deesser * 100),
            (self.compression, tune.compression * 100),
            (self.presence, tune.presence_db),
            (self.output, tune.output_db),
        ):
            widget.setValue(value)
        self._syncing = False
        self.pitch_view.set_settings(tune)
        self._sync_root_keys()
        self.refresh_takes()
        self.refresh_comps()

    def _record_settings_changed(self, *_):
        return vocal_recording._record_settings_changed(self, *_)

    def _tune_settings_changed(self, *_):
        return vocal_recording._tune_settings_changed(self, *_)

    def _sync_root_keys(self):
        return vocal_recording._sync_root_keys(self)

    def _apply_preset(self, name: str):
        return vocal_recording._apply_preset(self, name)

    # ── recording ─────────────────────────────────────────
    def scan_inputs(self):
        return vocal_recording.scan_inputs(self)

    def toggle_recording(self):
        return vocal_recording.toggle_recording(self)

    def _restore_count_in_transport(self):
        return vocal_recording._restore_count_in_transport(self)

    def _start_after_count(self, token: int):
        return vocal_recording._start_after_count(self, token)

    def _start_capture(self):
        return vocal_recording._start_capture(self)

    def _pause_changed(self, paused: bool):
        return vocal_recording._pause_changed(self, paused)

    def stop_recording(self):
        return vocal_recording.stop_recording(self)

    def discard_recording(self):
        return vocal_recording.discard_recording(self)

    def _finish_record_controls(self):
        return vocal_recording._finish_record_controls(self)

    # ── takes and processing ──────────────────────────────
    def refresh_takes(self, select: str | None = None):
        return vocal_takes.refresh_takes(self, select)

    def _related_take_ids(self, clip_id: str | None = None) -> tuple[str | None, str | None]:
        return vocal_takes._related_take_ids(self, clip_id)

    def _take_selection_changed(self, *_args):
        return vocal_takes._take_selection_changed(self, *_args)

    def rename_selected_take(self):
        return vocal_takes.rename_selected_take(self)

    def duplicate_selected_take(self):
        return vocal_takes.duplicate_selected_take(self)

    def delete_selected_take(self):
        return vocal_takes.delete_selected_take(self)

    def audition_related_dry(self):
        return vocal_takes.audition_related_dry(self)

    def audition_related_tuned(self):
        return vocal_takes.audition_related_tuned(self)

    # ── non-destructive comping ──────────────────────────
    def _current_comp(self) -> VocalComp | None:
        return vocal_comp_actions._current_comp(self)

    def refresh_comps(self, select: str | None = None):
        return vocal_comp_actions.refresh_comps(self, select)

    def create_comp(self):
        return vocal_comp_actions.create_comp(self)

    def _comp_selection_changed(self, *_args):
        return vocal_comp_actions._comp_selection_changed(self, *_args)

    def _refresh_comp_regions(self, select: str | None = None):
        return vocal_comp_actions._refresh_comp_regions(self, select)

    def _selected_comp_region(self) -> VocalCompRegion | None:
        return vocal_comp_actions._selected_comp_region(self)

    def _comp_region_selection_changed(self, *_args):
        return vocal_comp_actions._comp_region_selection_changed(self, *_args)

    def add_comp_region(self):
        return vocal_comp_actions.add_comp_region(self)

    def update_comp_region(self):
        return vocal_comp_actions.update_comp_region(self)

    def move_comp_region(self, direction: int):
        return vocal_comp_actions.move_comp_region(self, direction)

    def remove_comp_region(self):
        return vocal_comp_actions.remove_comp_region(self)

    def _render_current_comp(self):
        return vocal_comp_actions._render_current_comp(self)

    def render_comp(self):
        return vocal_comp_actions.render_comp(self)

    def audition_comp(self):
        return vocal_comp_actions.audition_comp(self)

    def place_comp(self):
        return vocal_comp_actions.place_comp(self)

    def use_browser_selection(self):
        self._song_clip_id = self._song_source_id = None
        clip_id = self.app.current_clip
        if not clip_id or clip_id not in self.app.library.clips:
            self.analysis_label.setText("Select an audio clip in the Browser first.")
            return
        if self.take_box.findData(clip_id) < 0:
            clip = self.app.library.clips[clip_id]
            self.take_box.addItem(f"SOURCE · {clip.name} · {clip.duration:.1f}s", clip_id)
        self.take_box.setCurrentIndex(self.take_box.findData(clip_id))

    def audition_source(self):
        clip_id = self.take_box.currentData()
        if clip_id:
            self.app.engine.audition(clip_id, *self.pitch_view.selection)

    def detect_source_key(self):
        return vocal_processing.detect_source_key(self, analyze=analyze_pitch)

    def render_take(self):
        return vocal_processing.render_take(self)

    def _key_progress(self, job_id: int, value: int):
        return vocal_processing._key_progress(self, job_id, value)

    def _key_finished(self, job_id: int, analysis, key: str, scale: str, confidence: float):
        return vocal_processing._key_finished(self, job_id, analysis, key, scale, confidence)

    def _key_failed(self, job_id: int, message: str):
        return vocal_processing._key_failed(self, job_id, message)

    def _tune_progress(self, job_id: int, value: int):
        return vocal_processing._tune_progress(self, job_id, value)

    def _tune_finished(self, job_id: int, audio, analysis, name: str):
        return vocal_processing._tune_finished(self, job_id, audio, analysis, name)

    def _tune_failed_after_save(self, message: str):
        return vocal_processing._tune_failed_after_save(self, message)

    def _tune_failed(self, job_id: int, message: str):
        return vocal_processing._tune_failed(self, job_id, message)

    def place_selected_take(self):
        clip_id = self.take_box.currentData()
        if clip_id:
            self._place_clip(clip_id, float(self.app.engine.beat))

    def _place_clip(self, clip_id: str, beat: float, *, compensate_latency: bool = False):
        source = self.app.library.clips.get(clip_id)
        if source is None:
            return
        rec = self.app.project.vocal_record
        self.app.snapshot()
        self.app._ensure_playlist_rows(rec.playlist_row + 1)
        length_beats = max(0.25, source.duration * self.app.project.bpm / 60.0)
        latency_beats = (
            max(0.0, float(rec.input_latency_ms)) * self.app.project.bpm / 60_000.0
            if compensate_latency
            else 0.0
        )
        self.app.project.rows[rec.playlist_row].clips.append(
            Clip(
                kind="audio",
                ref=clip_id,
                start_beat=max(0.0, beat - latency_beats),
                length_beats=length_beats,
                source_length=source.duration,
                track=rec.mixer_track,
            )
        )
        self.app.playlist.refresh()
        self.app._refresh_place_box()
        self.app.status.showMessage(
            f"placed {source.name} on Playlist lane {rec.playlist_row + 1}", 3500
        )

    def _tick(self):
        peak = float(self.recorder.input_peak)
        db = 20.0 * np.log10(max(peak, 1e-6))
        self.input_meter.setValue(int(np.clip((db + 60.0) / 60.0, 0, 1) * 1000))
        self.input_meter.setFormat(f"INPUT  {db:.1f} dBFS")
        elapsed = self.recorder.elapsed if self.recorder.recording else 0.0
        minutes, seconds = divmod(elapsed, 60.0)
        self.record_time.setText(f"{int(minutes):02d}:{seconds:04.1f}")

    def shutdown(self):
        self.pitch_view.shutdown()
        self.meter_timer.stop()
        self._countdown_token += 1
        self._restore_count_in_transport()
        self._key_job_id += 1
        if self._key_cancel is not None:
            self._key_cancel.set()
            self._key_cancel = None
        self._tune_job_id += 1
        if self._tune_cancel is not None:
            self._tune_cancel.set()
            self._tune_cancel = None
        if self.recorder.recording:
            self.recorder.discard()
