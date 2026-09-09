"""Vocal recording and pitch-correction workspace."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import numpy as np
from .window_client import WindowClient, emit_if_alive

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QProgressBar,
    QGroupBox,
    QLineEdit,
    QScrollArea,
    QMessageBox,
    QInputDialog,
)

from ..model import Clip, NTRACKS, VocalComp, VocalCompRegion
from ..vocal import (
    NOTE_NAMES,
    SCALES,
    ProcessingCancelled,
    VocalRecorder,
    analyze_pitch,
    detect_key,
    input_device_inventory,
    note_name,
    render_autotune,
)
from .theme import label_font


def _small(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    return label


class VocalPanel(WindowClient, QWidget):
    """A complete take → tune → arrange workflow in one tab."""

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
        self._syncing = True
        self._batch_tune_edit = False
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
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 12, 14, 18)
        layout.setSpacing(12)

        title = QLabel("TAKES · COMP · TUNING")
        title.setObjectName("title")
        title.setFont(label_font(12, bold=True))
        layout.addWidget(title)
        layout.addWidget(
            _small(
                "Record an unaltered 24-bit take, then render pitch correction to a "
                "new clip. Your original performance is always kept."
            )
        )
        recording = self._record_group()
        recording.hide()
        advanced_capture = QPushButton("Advanced capture settings")
        advanced_capture.setCheckable(True)
        advanced_capture.toggled.connect(recording.setVisible)
        layout.addWidget(advanced_capture)
        layout.addWidget(recording)
        layout.addWidget(self._tune_group())
        layout.addWidget(self._comp_group())
        layout.addStretch(1)
        scroller.setWidget(body)
        outer.addWidget(scroller)

    def _record_group(self) -> QGroupBox:
        box = QGroupBox("1 · RECORD VOCALS")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(8)

        grid.addWidget(_small("INPUT"), 0, 0)
        self.input_box = QComboBox()
        self.input_box.addItem("System default input", "")
        self.input_box.setMinimumWidth(260)
        self.input_box.currentIndexChanged.connect(self._record_settings_changed)
        grid.addWidget(self.input_box, 0, 1, 1, 3)
        scan = QPushButton("RESCAN")
        scan.setObjectName("mini")
        scan.clicked.connect(self.scan_inputs)
        grid.addWidget(scan, 0, 4)

        grid.addWidget(_small("TAKE NAME"), 1, 0)
        self.take_name = QLineEdit("Lead Vocal")
        grid.addWidget(self.take_name, 1, 1, 1, 2)
        grid.addWidget(_small("INPUT GAIN"), 1, 3)
        self.input_gain = QDoubleSpinBox()
        self.input_gain.setRange(-24.0, 24.0)
        self.input_gain.setDecimals(1)
        self.input_gain.setSuffix(" dB")
        self.input_gain.valueChanged.connect(self._record_settings_changed)
        grid.addWidget(self.input_gain, 1, 4)

        grid.addWidget(_small("COUNT-IN"), 2, 0)
        self.count_in = QComboBox()
        for bars in range(5):
            self.count_in.addItem(
                "off" if bars == 0 else f"{bars} bar" + ("s" if bars > 1 else ""), bars
            )
        self.count_in.currentIndexChanged.connect(self._record_settings_changed)
        grid.addWidget(self.count_in, 2, 1)
        self.monitor = QCheckBox("DRY MONITOR")
        self.monitor.setToolTip(
            "Low-latency software monitoring. Use headphones to prevent feedback."
        )
        self.monitor.toggled.connect(self._record_settings_changed)
        grid.addWidget(self.monitor, 2, 2)
        self.monitor_gain = QDoubleSpinBox()
        self.monitor_gain.setRange(0, 150)
        self.monitor_gain.setSuffix("% cue")
        self.monitor_gain.valueChanged.connect(self._record_settings_changed)
        grid.addWidget(self.monitor_gain, 2, 3)
        self.auto_place = QCheckBox("PLACE ON PLAYLIST")
        self.auto_place.toggled.connect(self._record_settings_changed)
        grid.addWidget(self.auto_place, 2, 4)

        grid.addWidget(_small("PLAYLIST LANE"), 3, 0)
        self.row_box = QSpinBox()
        self.row_box.setRange(1, 128)
        self.row_box.valueChanged.connect(self._record_settings_changed)
        grid.addWidget(self.row_box, 3, 1)
        grid.addWidget(_small("MIXER TRACK"), 3, 2)
        self.track_box = QComboBox()
        for index in range(NTRACKS):
            self.track_box.addItem(f"{index + 1} · {self.app.project.tracks[index].name}", index)
        self.track_box.currentIndexChanged.connect(self._record_settings_changed)
        grid.addWidget(self.track_box, 3, 3, 1, 2)

        grid.addWidget(_small("INPUT LATENCY"), 4, 0)
        self.input_latency = QDoubleSpinBox()
        self.input_latency.setRange(0.0, 500.0)
        self.input_latency.setDecimals(1)
        self.input_latency.setSuffix(" ms")
        self.input_latency.setToolTip(
            "Measured input delay removed when the recorded take is placed."
        )
        self.input_latency.valueChanged.connect(self._record_settings_changed)
        grid.addWidget(self.input_latency, 4, 1)
        grid.addWidget(
            _small("Set this to the measured loopback/input delay; the dry audio stays unchanged."),
            4,
            2,
            1,
            3,
        )

        self.record_button = QPushButton("●  START VOCAL TAKE")
        self.record_button.setObjectName("rec")
        self.record_button.clicked.connect(self.toggle_recording)
        grid.addWidget(self.record_button, 5, 0, 1, 2)
        self.pause_button = QPushButton("PAUSE")
        self.pause_button.setObjectName("mini")
        self.pause_button.setCheckable(True)
        self.pause_button.setEnabled(False)
        self.pause_button.toggled.connect(self._pause_changed)
        grid.addWidget(self.pause_button, 5, 2)
        self.discard_button = QPushButton("DISCARD")
        self.discard_button.setObjectName("mini")
        self.discard_button.setEnabled(False)
        self.discard_button.clicked.connect(self.discard_recording)
        grid.addWidget(self.discard_button, 5, 3)
        self.record_time = QLabel("00:00.0")
        self.record_time.setObjectName("counter")
        grid.addWidget(self.record_time, 5, 4)

        self.input_meter = QProgressBar()
        self.input_meter.setRange(0, 1000)
        self.input_meter.setTextVisible(True)
        self.input_meter.setFormat("INPUT  −∞ dBFS")
        self.input_meter.setToolTip("Aim for peaks around −12 to −6 dBFS; avoid 0 dBFS.")
        grid.addWidget(self.input_meter, 6, 0, 1, 5)
        self.record_status = _small(
            "Choose RESCAN to list microphones, or record from the system default."
        )
        grid.addWidget(self.record_status, 7, 0, 1, 5)
        return box

    def _tune_group(self) -> QGroupBox:
        box = QGroupBox("2 · AUTOTUNE + VOCAL CHAIN")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(8)

        grid.addWidget(_small("SOURCE TAKE"), 0, 0)
        self.take_box = QComboBox()
        self.take_box.setMinimumWidth(250)
        self.take_box.currentIndexChanged.connect(self._take_selection_changed)
        grid.addWidget(self.take_box, 0, 1, 1, 3)
        use_selected = QPushButton("USE BROWSER SELECTION")
        use_selected.setObjectName("mini")
        use_selected.clicked.connect(self.use_browser_selection)
        grid.addWidget(use_selected, 0, 4)

        grid.addWidget(_small("PRESET"), 1, 0)
        self.preset = QComboBox()
        self.preset.addItems(["Custom", "Natural vocal", "Modern vocal", "Hard tune", "Rap lead"])
        self.preset.currentTextChanged.connect(self._apply_preset)
        grid.addWidget(self.preset, 1, 1)
        grid.addWidget(_small("KEY"), 1, 2)
        self.key_box = QComboBox()
        self.key_box.addItems(NOTE_NAMES)
        self.key_box.currentTextChanged.connect(self._tune_settings_changed)
        grid.addWidget(self.key_box, 1, 3)
        self.detect_key_button = QPushButton("DETECT KEY")
        self.detect_key_button.setObjectName("mini")
        self.detect_key_button.clicked.connect(self.detect_source_key)
        grid.addWidget(self.detect_key_button, 1, 4)

        grid.addWidget(_small("SCALE"), 2, 0)
        self.scale_box = QComboBox()
        self.scale_box.addItems(list(SCALES))
        self.scale_box.currentTextChanged.connect(self._tune_settings_changed)
        grid.addWidget(self.scale_box, 2, 1)
        grid.addWidget(_small("VOCAL RANGE"), 2, 2)
        self.range_box = QComboBox()
        self.range_box.addItem("Bass · C2–C4", (36, 60))
        self.range_box.addItem("Tenor · C3–C5", (48, 72))
        self.range_box.addItem("Alto · F3–F5", (53, 77))
        self.range_box.addItem("Soprano · C4–C6", (60, 84))
        self.range_box.addItem("Wide · C2–C6", (36, 84))
        self.range_box.currentIndexChanged.connect(self._tune_settings_changed)
        grid.addWidget(self.range_box, 2, 3)
        self.autotune_enabled = QCheckBox("AUTOTUNE ON")
        self.autotune_enabled.toggled.connect(self._tune_settings_changed)
        grid.addWidget(self.autotune_enabled, 2, 4)

        self.strength = self._parameter(grid, 3, "CORRECTION", 0, 100, "%")
        self.retune = self._parameter(grid, 4, "RETUNE", 0, 250, " ms")
        self.humanize = self._parameter(grid, 5, "HUMANIZE", 0, 100, "%")
        self.mix = self._parameter(grid, 6, "WET / DRY", 0, 100, "%")
        self.formant = self._parameter(grid, 7, "FORMANT BODY", 0, 100, "%")
        self.transpose = self._parameter(grid, 8, "TRANSPOSE", -12, 12, " st")
        self.gate = self._parameter(grid, 9, "NOISE GATE", -80, -20, " dB")
        self.highpass = self._parameter(grid, 10, "HIGH-PASS", 20, 300, " Hz")
        self.deesser = self._parameter(grid, 11, "DE-ESSER", 0, 100, "%")
        self.compression = self._parameter(grid, 12, "COMPRESSION", 0, 100, "%")
        self.presence = self._parameter(grid, 13, "PRESENCE", -6, 9, " dB")
        self.output = self._parameter(grid, 14, "OUTPUT", -18, 12, " dB")

        self.preview_original = QPushButton("▶ ORIGINAL")
        self.preview_original.setObjectName("mini")
        self.preview_original.clicked.connect(self.audition_source)
        grid.addWidget(self.preview_original, 15, 0)
        self.render_button = QPushButton("✦ TUNE → NEW TAKE")
        self.render_button.setObjectName("go")
        self.render_button.clicked.connect(self.render_take)
        grid.addWidget(self.render_button, 15, 1, 1, 2)
        self.place_button = QPushButton("PLACE TAKE")
        self.place_button.setObjectName("mini")
        self.place_button.clicked.connect(self.place_selected_take)
        grid.addWidget(self.place_button, 15, 3)
        self.stop_preview = QPushButton("■ STOP PREVIEW")
        self.stop_preview.setObjectName("mini")
        self.stop_preview.clicked.connect(self.app.engine.stop_audition)
        grid.addWidget(self.stop_preview, 15, 4)

        self.rename_take_button = QPushButton("RENAME")
        self.rename_take_button.setObjectName("mini")
        self.rename_take_button.clicked.connect(self.rename_selected_take)
        grid.addWidget(self.rename_take_button, 16, 0)
        self.duplicate_take_button = QPushButton("DUPLICATE")
        self.duplicate_take_button.setObjectName("mini")
        self.duplicate_take_button.clicked.connect(self.duplicate_selected_take)
        grid.addWidget(self.duplicate_take_button, 16, 1)
        self.delete_take_button = QPushButton("DELETE")
        self.delete_take_button.setObjectName("mini")
        self.delete_take_button.clicked.connect(self.delete_selected_take)
        grid.addWidget(self.delete_take_button, 16, 2)
        self.compare_dry_button = QPushButton("A/B DRY")
        self.compare_dry_button.setObjectName("mini")
        self.compare_dry_button.clicked.connect(self.audition_related_dry)
        grid.addWidget(self.compare_dry_button, 16, 3)
        self.compare_tuned_button = QPushButton("A/B TUNED")
        self.compare_tuned_button.setObjectName("mini")
        self.compare_tuned_button.clicked.connect(self.audition_related_tuned)
        grid.addWidget(self.compare_tuned_button, 16, 4)

        self.key_progress = QProgressBar()
        self.key_progress.setRange(0, 100)
        self.key_progress.setValue(0)
        self.key_progress.setFormat("KEY DETECTION READY")
        grid.addWidget(self.key_progress, 17, 0, 1, 5)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("READY")
        grid.addWidget(self.progress, 18, 0, 1, 5)
        self.analysis_label = _small(
            "Correction supports chromatic, major, minor and pentatonic scales. "
            "Use a dry, single-note vocal for the cleanest tracking."
        )
        grid.addWidget(self.analysis_label, 19, 0, 1, 5)
        return box

    def _parameter(
        self, grid: QGridLayout, row: int, name: str, minimum: float, maximum: float, suffix: str
    ) -> QDoubleSpinBox:
        column = 0 if row % 2 else 2
        actual_row = 3 + (row - 3) // 2
        grid.addWidget(_small(name), actual_row, column)
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(1)
        box.setSuffix(suffix)
        box.valueChanged.connect(self._tune_settings_changed)
        grid.addWidget(box, actual_row, column + 1)
        return box

    def _comp_group(self) -> QGroupBox:
        box = QGroupBox("3 · NON-DESTRUCTIVE VOCAL COMP")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(8)

        grid.addWidget(_small("COMP"), 0, 0)
        self.comp_box = QComboBox()
        self.comp_box.currentIndexChanged.connect(self._comp_selection_changed)
        grid.addWidget(self.comp_box, 0, 1, 1, 2)
        self.comp_name = QLineEdit("Vocal Comp")
        self.comp_name.setPlaceholderText("New comp name")
        grid.addWidget(self.comp_name, 0, 3)
        self.new_comp_button = QPushButton("NEW COMP")
        self.new_comp_button.setObjectName("mini")
        self.new_comp_button.clicked.connect(self.create_comp)
        grid.addWidget(self.new_comp_button, 0, 4)

        grid.addWidget(_small("SOURCE RANGE"), 1, 0)
        self.comp_source_start = QDoubleSpinBox()
        self.comp_source_start.setRange(0.0, 36_000.0)
        self.comp_source_start.setDecimals(3)
        self.comp_source_start.setSuffix(" s start")
        grid.addWidget(self.comp_source_start, 1, 1)
        self.comp_source_end = QDoubleSpinBox()
        self.comp_source_end.setRange(0.0, 36_000.0)
        self.comp_source_end.setDecimals(3)
        self.comp_source_end.setSuffix(" s end")
        grid.addWidget(self.comp_source_end, 1, 2)
        self.comp_timeline_start = QDoubleSpinBox()
        self.comp_timeline_start.setRange(0.0, 36_000.0)
        self.comp_timeline_start.setDecimals(3)
        self.comp_timeline_start.setSuffix(" s on comp")
        grid.addWidget(self.comp_timeline_start, 1, 3)
        self.add_region_button = QPushButton("ADD SELECTED TAKE")
        self.add_region_button.setObjectName("go")
        self.add_region_button.setToolTip(
            "Add this range from the dry/tuned SOURCE TAKE selected above."
        )
        self.add_region_button.clicked.connect(self.add_comp_region)
        grid.addWidget(self.add_region_button, 1, 4)

        grid.addWidget(_small("REGIONS"), 2, 0)
        self.comp_region_box = QComboBox()
        self.comp_region_box.currentIndexChanged.connect(self._comp_region_selection_changed)
        grid.addWidget(self.comp_region_box, 2, 1, 1, 2)
        self.apply_region_button = QPushButton("APPLY TRIM / POSITION")
        self.apply_region_button.setObjectName("mini")
        self.apply_region_button.clicked.connect(self.update_comp_region)
        grid.addWidget(self.apply_region_button, 2, 3)
        self.remove_region_button = QPushButton("REMOVE")
        self.remove_region_button.setObjectName("mini")
        self.remove_region_button.clicked.connect(self.remove_comp_region)
        grid.addWidget(self.remove_region_button, 2, 4)

        self.move_region_up_button = QPushButton("MOVE UP")
        self.move_region_up_button.setObjectName("mini")
        self.move_region_up_button.clicked.connect(lambda: self.move_comp_region(-1))
        grid.addWidget(self.move_region_up_button, 3, 1)
        self.move_region_down_button = QPushButton("MOVE DOWN")
        self.move_region_down_button.setObjectName("mini")
        self.move_region_down_button.clicked.connect(lambda: self.move_comp_region(1))
        grid.addWidget(self.move_region_down_button, 3, 2)
        self.audition_comp_button = QPushButton("▶ AUDITION COMP")
        self.audition_comp_button.setObjectName("mini")
        self.audition_comp_button.clicked.connect(self.audition_comp)
        grid.addWidget(self.audition_comp_button, 3, 3)
        self.render_comp_button = QPushButton("RENDER → NEW CLIP")
        self.render_comp_button.setObjectName("go")
        self.render_comp_button.clicked.connect(self.render_comp)
        grid.addWidget(self.render_comp_button, 3, 4)

        self.comp_status = _small(
            "Select a dry or tuned take above, set a source range and place it on the comp."
        )
        grid.addWidget(self.comp_status, 4, 0, 1, 4)
        self.place_comp_button = QPushButton("PLACE COMP")
        self.place_comp_button.setObjectName("mini")
        self.place_comp_button.clicked.connect(self.place_comp)
        grid.addWidget(self.place_comp_button, 4, 4)
        return box

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
        self.refresh_takes()
        self.refresh_comps()

    def _record_settings_changed(self, *_):
        if self._syncing:
            return
        self.app.snapshot()
        rec = self.app.project.vocal_record
        rec.input_device = str(self.input_box.currentData() or "")
        rec.input_gain_db = self.input_gain.value()
        rec.input_latency_ms = self.input_latency.value()
        rec.monitor = self.monitor.isChecked()
        rec.monitor_gain = self.monitor_gain.value() / 100.0
        rec.count_in_bars = int(self.count_in.currentData() or 0)
        rec.auto_place = self.auto_place.isChecked()
        rec.playlist_row = self.row_box.value() - 1
        rec.mixer_track = int(self.track_box.currentData() or 0)
        self.app._set_dirty(True)

    def _tune_settings_changed(self, *_):
        if self._syncing:
            return
        if not self._batch_tune_edit:
            self.app.snapshot()
        tune = self.app.project.vocal
        tune.enabled = self.autotune_enabled.isChecked()
        tune.key = self.key_box.currentText()
        tune.scale = self.scale_box.currentText()
        tune.low_note, tune.high_note = self.range_box.currentData() or (36, 84)
        tune.strength = self.strength.value() / 100.0
        tune.retune_ms = self.retune.value()
        tune.humanize = self.humanize.value() / 100.0
        tune.mix = self.mix.value() / 100.0
        tune.formant = self.formant.value() / 100.0
        tune.transpose = int(self.transpose.value())
        tune.gate_db = self.gate.value()
        tune.highpass_hz = self.highpass.value()
        tune.deesser = self.deesser.value() / 100.0
        tune.compression = self.compression.value() / 100.0
        tune.presence_db = self.presence.value()
        tune.output_db = self.output.value()
        self.app._set_dirty(True)

    def _apply_preset(self, name: str):
        presets = {
            "Natural vocal": (75, 80, 45, 100, 90, 25, 30, 1.5),
            "Modern vocal": (95, 22, 15, 100, 70, 38, 55, 2.5),
            "Hard tune": (100, 0, 0, 100, 35, 45, 70, 3.5),
            "Rap lead": (92, 12, 8, 100, 55, 55, 65, 3.0),
        }
        values = presets.get(name)
        if values is None:
            return
        if not self._syncing:
            self.app.snapshot()
        self._batch_tune_edit = True
        try:
            for widget, value in zip(
                (
                    self.strength,
                    self.retune,
                    self.humanize,
                    self.mix,
                    self.formant,
                    self.deesser,
                    self.compression,
                    self.presence,
                ),
                values,
                strict=True,
            ):
                widget.setValue(value)
            self._tune_settings_changed()
        finally:
            self._batch_tune_edit = False

    # ── recording ─────────────────────────────────────────
    def scan_inputs(self):
        try:
            inputs, default = input_device_inventory()
        except Exception as exc:
            self.record_status.setText(f"input scan failed · {exc}")
            return
        wanted = self.app.project.vocal_record.input_device
        self._inputs = inputs
        self.input_box.blockSignals(True)
        self.input_box.clear()
        default_name = next(
            (item["name"] for item in inputs if item["index"] == default), "system default"
        )
        self.input_box.addItem(f"System default · {default_name}", "")
        for item in inputs:
            self.input_box.addItem(item["label"], item["key"])
        index = self.input_box.findData(wanted)
        self.input_box.setCurrentIndex(max(0, index))
        self.input_box.blockSignals(False)
        self.record_status.setText(f"{len(inputs)} microphone input(s) available")

    def toggle_recording(self):
        capture = getattr(self.app, "track_capture", None)
        if capture is not None and capture.busy:
            self.record_status.setText("Stop and save the Song track take first")
            return
        if self.recorder.recording or self.recorder.temporary_path is not None:
            self.stop_recording()
            return
        if self._counting:
            self._countdown_token += 1
            self._counting = False
            self._restore_count_in_transport()
            self.record_button.setText("●  START VOCAL TAKE")
            self.record_status.setText("count-in cancelled")
            return
        self._countdown_token += 1
        bars = int(self.count_in.currentData() or 0)
        if bars:
            token = self._countdown_token
            seconds = bars * 4.0 * 60.0 / self.app.project.bpm
            self._count_in_transport = (
                bool(self.app.engine.playing),
                bool(self.app.engine.metronome),
            )
            self.app.engine.metronome = True
            self.app.btn_metro.setChecked(True)
            if not self.app.engine.playing:
                self.app.engine.play()
            self._counting = True
            self.record_button.setText(
                f"CANCEL · COUNTING {bars} BAR" + ("S" if bars > 1 else "") + "…"
            )
            self.record_status.setText("count-in running · recording starts after the downbeat")
            QTimer.singleShot(int(seconds * 1000), self, lambda: self._start_after_count(token))
        else:
            self._start_capture()

    def _restore_count_in_transport(self):
        """Undo only transport changes made for an interrupted count-in."""
        previous, self._count_in_transport = self._count_in_transport, None
        if previous is None:
            return
        was_playing, had_metronome = previous
        self.app.engine.metronome = had_metronome
        self.app.btn_metro.setChecked(had_metronome)
        if not was_playing:
            # Stop even when the engine has not consumed the queued play yet;
            # command ordering guarantees the count-in play cannot leak later.
            self.app.engine.stop_transport(False)
        elif not self.app.engine.playing:
            self.app.engine.play()

    def _start_after_count(self, token: int):
        if token != self._countdown_token:
            return
        self._counting = False
        self._start_capture()

    def _start_capture(self):
        rec = self.app.project.vocal_record
        selected = next((item for item in self._inputs if item["key"] == rec.input_device), None)
        device = selected["index"] if selected is not None else None
        monitor_callback = (
            (lambda block: self.app.engine.queue_monitor(block, rec.monitor_gain))
            if rec.monitor
            else None
        )
        try:
            self.recorder.start(device, rec.input_gain_db, monitor_callback)
        except Exception as exc:
            self._restore_count_in_transport()
            self.record_button.setEnabled(True)
            self.record_button.setText("●  START VOCAL TAKE")
            self.record_status.setText(f"recording failed · {exc}")
            QMessageBox.warning(self, "Vocal input failed", str(exc))
            return
        # Successful capture owns the transport state from here onward.
        self._count_in_transport = None
        self._record_start_beat = float(self.app.engine.beat)
        self.record_button.setText("■  STOP + SAVE TAKE")
        self.pause_button.setEnabled(True)
        self.discard_button.setEnabled(True)
        self.record_status.setText("recording 24-bit library take · original stays dry")

    def _pause_changed(self, paused: bool):
        self.recorder.paused = bool(paused)
        self.pause_button.setText("RESUME" if paused else "PAUSE")

    def stop_recording(self):
        try:
            audio = self.recorder.stop()
        except Exception as exc:
            self._finish_record_controls()
            self.record_status.setText(f"capture failed · {exc}")
            QMessageBox.warning(self, "Save take failed", str(exc))
            return
        self._finish_record_controls()
        if len(audio) < int(self.app.engine.sr * 0.08):
            self.recorder.discard()
            self._finish_record_controls()
            self.record_status.setText("take was too short and was not saved")
            return
        stamp = time.strftime("%Y-%m-%d %H%M%S")
        name = self.take_name.text().strip() or f"Vocal {stamp}"
        previous_redo = list(getattr(self.app, "_redo", []))
        was_dirty = getattr(self.app, "_dirty", False)
        self.app.snapshot()
        try:
            clip = self.app.library.add_audio(audio, name, kind="vocal")
        except Exception as exc:
            if hasattr(self.app, "discard_snapshot"):
                self.app.discard_snapshot()
                self.app._redo[:] = previous_redo
                self.app._set_dirty(was_dirty)
                self.app._try_save_history()
            self.record_status.setText(f"Take retained · retry save · {exc}")
            QMessageBox.warning(self, "Save take failed", str(exc))
            return
        self.recorder.commit()
        self._finish_record_controls()
        self._latest_clip = clip.id
        self.refresh_takes(select=clip.id)
        if self.auto_place.isChecked():
            self._place_clip(clip.id, self._record_start_beat, compensate_latency=True)
        self.app._library_changed()
        self.record_status.setText(
            f"saved {clip.name} · {clip.duration:.1f}s"
            + (f" · {self.recorder.overruns} input overflow(s)" if self.recorder.overruns else "")
        )

    def discard_recording(self):
        self._countdown_token += 1
        self._restore_count_in_transport()
        try:
            self.recorder.discard()
        except Exception as exc:
            self.record_status.setText(f"discard cleanup failed · {exc}")
        self._finish_record_controls()
        if "failed" not in self.record_status.text():
            self.record_status.setText("take discarded · nothing was written")

    def _finish_record_controls(self):
        self._counting = False
        self.record_button.setEnabled(True)
        self.record_button.setText("●  START VOCAL TAKE")
        self.pause_button.blockSignals(True)
        self.pause_button.setChecked(False)
        self.pause_button.blockSignals(False)
        self.pause_button.setText("PAUSE")
        self.pause_button.setEnabled(False)
        pending = self.recorder.temporary_path is not None
        self.discard_button.setEnabled(pending)
        if pending:
            self.record_button.setText("RETRY SAVE TAKE")

    # ── takes and processing ──────────────────────────────
    def refresh_takes(self, select: str | None = None):
        wanted = select or self.take_box.currentData() or self._latest_clip
        self.take_box.blockSignals(True)
        self.take_box.clear()
        for clip in self.app.library.ordered():
            if clip.kind in ("vocal", "vocal-tuned", "recording"):
                badge = "TUNED" if clip.kind == "vocal-tuned" else "DRY"
                self.take_box.addItem(f"{badge} · {clip.name} · {clip.duration:.1f}s", clip.id)
        index = self.take_box.findData(wanted)
        if index >= 0:
            self.take_box.setCurrentIndex(index)
        self.take_box.blockSignals(False)
        self._take_selection_changed()

    def _related_take_ids(self, clip_id: str | None = None) -> tuple[str | None, str | None]:
        clip_id = clip_id or self.take_box.currentData()
        selected = self.app.library.clips.get(clip_id)
        if selected is None:
            return None, None
        if selected.kind == "vocal-tuned":
            dry = selected.parent if selected.parent in self.app.library.clips else None
            return dry, selected.id
        if selected.kind != "vocal":
            return selected.id, None
        tuned = sorted(
            (
                clip
                for clip in self.app.library.clips.values()
                if clip.kind == "vocal-tuned" and clip.parent == selected.id
            ),
            key=lambda clip: clip.created,
            reverse=True,
        )
        return selected.id, tuned[0].id if tuned else None

    def _take_selection_changed(self, *_args):
        clip_id = self.take_box.currentData()
        clip = self.app.library.clips.get(clip_id)
        manageable = bool(clip and clip.kind in ("vocal", "vocal-tuned"))
        dry, tuned = self._related_take_ids(clip_id)
        for button in (
            self.rename_take_button,
            self.duplicate_take_button,
            self.delete_take_button,
        ):
            button.setEnabled(manageable)
        self.compare_dry_button.setEnabled(dry is not None)
        self.compare_tuned_button.setEnabled(tuned is not None)
        if hasattr(self, "comp_source_end") and clip is not None:
            if self.comp_region_box.currentIndex() < 0:
                self.comp_source_start.setValue(0.0)
                self.comp_source_end.setValue(float(clip.duration))

    def rename_selected_take(self):
        clip_id = self.take_box.currentData()
        clip = self.app.library.clips.get(clip_id)
        if clip is None or clip.kind not in ("vocal", "vocal-tuned"):
            return
        name, accepted = QInputDialog.getText(
            self, "Rename vocal take", "Take name", text=clip.name
        )
        if not accepted or not name.strip():
            return
        self.app.snapshot()
        try:
            self.app.library.rename(clip.id, name.strip())
        except Exception as exc:
            if hasattr(self.app, "discard_snapshot"):
                self.app.discard_snapshot()
            QMessageBox.warning(self, "Rename take failed", str(exc))
            return
        self.refresh_takes(select=clip.id)
        self.app._library_changed()
        self.app.status.showMessage(f"renamed take → {clip.name}", 3000)

    def duplicate_selected_take(self):
        clip_id = self.take_box.currentData()
        clip = self.app.library.clips.get(clip_id)
        audio = self.app.library.audio(clip_id) if clip is not None else None
        if clip is None or audio is None or clip.kind not in ("vocal", "vocal-tuned"):
            return
        self.app.snapshot()
        try:
            duplicate = self.app.library.add_audio(
                audio.copy(), f"{clip.name} copy", kind=clip.kind, parent=clip.parent
            )
        except Exception as exc:
            if hasattr(self.app, "discard_snapshot"):
                self.app.discard_snapshot()
            QMessageBox.warning(self, "Duplicate take failed", str(exc))
            return
        self._latest_clip = duplicate.id
        self.refresh_takes(select=duplicate.id)
        self.app._library_changed()
        self.app.status.showMessage(f"duplicated take → {duplicate.name}", 3000)

    def delete_selected_take(self):
        clip_id = self.take_box.currentData()
        clip = self.app.library.clips.get(clip_id)
        if clip is None or clip.kind not in ("vocal", "vocal-tuned"):
            return
        references = sum(
            block.kind == "audio" and block.ref == clip_id
            for row in self.app.project.rows
            for block in row.clips
        )
        comp_references = sum(
            region.source_id == clip_id
            for comp in self.app.project.vocal_comps
            for region in comp.regions
        )
        children = sum(item.parent == clip_id for item in self.app.library.clips.values())
        if references or children or comp_references:
            detail = []
            if references:
                detail.append(f"{references} Playlist placement(s)")
            if children:
                detail.append(f"{children} tuned take(s)")
            if comp_references:
                detail.append(f"{comp_references} vocal comp region(s)")
            QMessageBox.warning(
                self,
                "Take is still in use",
                f"Remove {', '.join(detail)} before deleting “{clip.name}”.",
            )
            return
        if (
            QMessageBox.question(
                self, "Delete vocal take", f"Move “{clip.name}” to recoverable library trash?"
            )
            != QMessageBox.Yes
        ):
            return
        self.app.snapshot()
        try:
            moved_to = self.app.library.delete(clip_id)
        except Exception as exc:
            if hasattr(self.app, "discard_snapshot"):
                self.app.discard_snapshot()
            QMessageBox.warning(self, "Delete take failed", str(exc))
            return
        self._latest_clip = None
        self.refresh_takes()
        self.app._library_changed()
        self.app.status.showMessage(f"take moved to trash → {moved_to}", 5000)

    def audition_related_dry(self):
        dry, _tuned = self._related_take_ids()
        if dry:
            self.app.engine.audition(dry, 0.0, 0.0)

    def audition_related_tuned(self):
        _dry, tuned = self._related_take_ids()
        if tuned:
            self.app.engine.audition(tuned, 0.0, 0.0)

    # ── non-destructive comping ──────────────────────────
    def _current_comp(self) -> VocalComp | None:
        comp_id = self.comp_box.currentData() if hasattr(self, "comp_box") else None
        return next((comp for comp in self.app.project.vocal_comps if comp.id == comp_id), None)

    def refresh_comps(self, select: str | None = None):
        wanted = select or self.app.project.current_vocal_comp or self.comp_box.currentData()
        self.comp_box.blockSignals(True)
        self.comp_box.clear()
        for comp in self.app.project.vocal_comps:
            self.comp_box.addItem(f"{comp.name} · {len(comp.regions)} region(s)", comp.id)
        index = self.comp_box.findData(wanted)
        if index < 0 and self.comp_box.count():
            index = 0
        self.comp_box.setCurrentIndex(index)
        self.comp_box.blockSignals(False)
        comp = self._current_comp()
        self.app.project.current_vocal_comp = comp.id if comp is not None else ""
        self._refresh_comp_regions()

    def create_comp(self):
        name = (
            self.comp_name.text().strip() or f"Vocal Comp {len(self.app.project.vocal_comps) + 1}"
        )
        self.app.snapshot()
        comp = VocalComp(name=name)
        self.app.project.vocal_comps.append(comp)
        self.app.project.current_vocal_comp = comp.id
        self.refresh_comps(select=comp.id)
        self.comp_status.setText(f"created {comp.name} · add regions from the selected take")

    def _comp_selection_changed(self, *_args):
        comp = self._current_comp()
        selected = comp.id if comp is not None else ""
        if selected != self.app.project.current_vocal_comp and not self._syncing:
            self.app._set_dirty(True)
        self.app.project.current_vocal_comp = selected
        self._refresh_comp_regions()

    def _refresh_comp_regions(self, select: str | None = None):
        comp = self._current_comp()
        wanted = select or self.comp_region_box.currentData()
        self.comp_region_box.blockSignals(True)
        self.comp_region_box.clear()
        if comp is not None:
            for number, region in enumerate(comp.regions, 1):
                source = self.app.library.clips.get(region.source_id)
                source_name = source.name if source is not None else "MISSING SOURCE"
                self.comp_region_box.addItem(
                    f"{number} · {source_name} · {region.source_start:.3f}–"
                    f"{region.source_end:.3f}s @ {region.timeline_start:.3f}s",
                    region.id,
                )
        index = self.comp_region_box.findData(wanted)
        if index < 0 and self.comp_region_box.count():
            index = 0
        self.comp_region_box.setCurrentIndex(index)
        self.comp_region_box.blockSignals(False)
        self._comp_region_selection_changed()
        available = comp is not None and bool(comp.regions)
        for button in (
            self.apply_region_button,
            self.remove_region_button,
            self.move_region_up_button,
            self.move_region_down_button,
            self.audition_comp_button,
            self.render_comp_button,
            self.place_comp_button,
        ):
            button.setEnabled(available)

    def _selected_comp_region(self) -> VocalCompRegion | None:
        comp = self._current_comp()
        region_id = self.comp_region_box.currentData()
        if comp is None:
            return None
        return next((region for region in comp.regions if region.id == region_id), None)

    def _comp_region_selection_changed(self, *_args):
        region = self._selected_comp_region()
        if region is None:
            return
        self.comp_source_start.setValue(region.source_start)
        self.comp_source_end.setValue(region.source_end)
        self.comp_timeline_start.setValue(region.timeline_start)
        take_index = self.take_box.findData(region.source_id)
        if take_index >= 0:
            self.take_box.setCurrentIndex(take_index)

    def add_comp_region(self):
        comp = self._current_comp()
        source_id = self.take_box.currentData()
        source = self.app.library.clips.get(source_id)
        if comp is None:
            self.comp_status.setText("Create or select a comp first.")
            return
        if source is None or source.kind not in ("vocal", "vocal-tuned"):
            self.comp_status.setText("Select a dry or tuned vocal take first.")
            return
        region = VocalCompRegion(
            source_id=source.id,
            source_start=self.comp_source_start.value(),
            source_end=self.comp_source_end.value(),
            timeline_start=self.comp_timeline_start.value(),
        )
        try:
            region.validate()
            if region.source_end > float(source.duration) + 0.0001:
                raise ValueError("source range extends past the end of the take")
        except ValueError as exc:
            self.comp_status.setText(f"region not added · {exc}")
            return
        self.app.snapshot()
        comp.regions.append(region)
        comp.rendered_clip_id = ""
        self.refresh_comps(select=comp.id)
        self._refresh_comp_regions(select=region.id)
        self.comp_timeline_start.setValue(region.timeline_start + region.duration)
        self.comp_status.setText(f"added {source.name} without changing the source take")

    def update_comp_region(self):
        comp = self._current_comp()
        region = self._selected_comp_region()
        source = self.app.library.clips.get(region.source_id) if region is not None else None
        if comp is None or region is None or source is None:
            return
        edited = VocalCompRegion(
            id=region.id,
            source_id=region.source_id,
            source_start=self.comp_source_start.value(),
            source_end=self.comp_source_end.value(),
            timeline_start=self.comp_timeline_start.value(),
        )
        try:
            edited.validate()
            if edited.source_end > float(source.duration) + 0.0001:
                raise ValueError("source range extends past the end of the take")
        except ValueError as exc:
            self.comp_status.setText(f"region not changed · {exc}")
            return
        self.app.snapshot()
        region.source_start = edited.source_start
        region.source_end = edited.source_end
        region.timeline_start = edited.timeline_start
        comp.rendered_clip_id = ""
        self.refresh_comps(select=comp.id)
        self._refresh_comp_regions(select=region.id)
        self.comp_status.setText("region trim and timeline position updated")

    def move_comp_region(self, direction: int):
        comp = self._current_comp()
        region = self._selected_comp_region()
        if comp is None or region is None:
            return
        old = comp.regions.index(region)
        new = max(0, min(len(comp.regions) - 1, old + int(direction)))
        if new == old:
            return
        self.app.snapshot()
        comp.regions.insert(new, comp.regions.pop(old))
        comp.rendered_clip_id = ""
        self.refresh_comps(select=comp.id)
        self._refresh_comp_regions(select=region.id)

    def remove_comp_region(self):
        comp = self._current_comp()
        region = self._selected_comp_region()
        if comp is None or region is None:
            return
        self.app.snapshot()
        comp.regions.remove(region)
        comp.rendered_clip_id = ""
        self.refresh_comps(select=comp.id)
        self.comp_status.setText("region removed · source take preserved")

    def _render_current_comp(self):
        comp = self._current_comp()
        if comp is None or not comp.regions:
            self.comp_status.setText("Add at least one region before rendering.")
            return None
        try:
            clip = self.app.library.render_vocal_comp(comp, f"{comp.name} render")
        except Exception as exc:
            self.comp_status.setText(f"comp render failed · {exc}")
            return None
        self.app.snapshot()
        comp.rendered_clip_id = clip.id
        self._latest_clip = clip.id
        self.app._library_changed()
        self.refresh_comps(select=comp.id)
        self.comp_status.setText(
            f"rendered {clip.name} · {clip.duration:.1f}s · source takes preserved"
        )
        return clip

    def render_comp(self):
        self._render_current_comp()

    def audition_comp(self):
        comp = self._current_comp()
        clip = self.app.library.clips.get(comp.rendered_clip_id) if comp is not None else None
        if clip is None or clip.comp_id != comp.id:
            clip = self._render_current_comp()
        if clip is not None:
            self.app.engine.audition(clip.id, 0.0, 0.0)

    def place_comp(self):
        comp = self._current_comp()
        clip = self.app.library.clips.get(comp.rendered_clip_id) if comp is not None else None
        if clip is None or clip.comp_id != comp.id:
            clip = self._render_current_comp()
        if clip is not None:
            self._place_clip(clip.id, float(self.app.engine.beat))

    def use_browser_selection(self):
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
            self.app.engine.audition(clip_id, 0.0, 0.0)

    def detect_source_key(self):
        if self._key_cancel is not None:
            self._key_cancel.set()
            self.detect_key_button.setEnabled(False)
            self.key_progress.setFormat("CANCELLING KEY DETECTION…")
            return
        clip_id = self.take_box.currentData()
        audio = self.app.library.audio(clip_id) if clip_id else None
        if audio is None:
            self.analysis_label.setText("Choose a source take first.")
            return
        settings = replace(self.app.project.vocal)
        self._key_job_id += 1
        job_id = self._key_job_id
        cancel = threading.Event()
        self._key_cancel = cancel
        self.detect_key_button.setText("CANCEL KEY DETECTION")
        self.detect_key_button.setEnabled(True)
        self.key_progress.setValue(0)
        self.key_progress.setFormat("ANALYSING KEY… %p%")
        self.analysis_label.setText("analysing vocal notes…")

        sample_rate = self.app.engine.sr

        def worker():
            try:
                analysis = analyze_pitch(
                    audio,
                    settings,
                    sample_rate,
                    progress=lambda value: emit_if_alive(
                        self, "keyProgress", job_id, int(value * 100)
                    ),
                    cancelled=cancel.is_set,
                )
                if cancel.is_set():
                    raise ProcessingCancelled("key detection cancelled")
                key, scale, confidence = detect_key(analysis)
                emit_if_alive(self, "keyFinished", job_id, analysis, key, scale, confidence)
            except Exception as exc:
                emit_if_alive(self, "keyFailed", job_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def render_take(self):
        if self._tune_cancel is not None:
            self._tune_cancel.set()
            self.render_button.setEnabled(False)
            self.progress.setFormat("CANCELLING TUNE…")
            return
        clip_id = self.take_box.currentData()
        audio = self.app.library.audio(clip_id) if clip_id else None
        if audio is None:
            self.analysis_label.setText("Choose a source take first.")
            return
        source_name = self.app.library.clips[clip_id].name
        settings = replace(self.app.project.vocal)
        self._tune_job_id += 1
        job_id = self._tune_job_id
        cancel = threading.Event()
        self._tune_cancel = cancel
        self._render_source_id = clip_id
        self.render_button.setText("CANCEL TUNE")
        self.render_button.setEnabled(True)
        self.progress.setValue(1)
        self.progress.setFormat("ANALYSING + TUNING… %p%")

        sample_rate = self.app.engine.sr

        def worker():
            try:
                rendered, analysis = render_autotune(
                    audio,
                    settings,
                    sample_rate,
                    lambda value: emit_if_alive(self, "tuneProgress", job_id, int(value * 100)),
                    cancel.is_set,
                )
                emit_if_alive(
                    self,
                    "tuneFinished",
                    job_id,
                    rendered,
                    analysis,
                    f"{source_name} · {settings.key} {settings.scale} tuned",
                )
            except Exception as exc:
                emit_if_alive(self, "tuneFailed", job_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _key_progress(self, job_id: int, value: int):
        if job_id == self._key_job_id and self._key_cancel is not None:
            self.key_progress.setValue(value)

    def _key_finished(self, job_id: int, analysis, key: str, scale: str, confidence: float):
        if job_id != self._key_job_id or self._key_cancel is None:
            return
        if self._key_cancel.is_set():
            self._key_failed(job_id, "key detection cancelled")
            return
        self._key_cancel = None
        self.detect_key_button.setText("DETECT KEY")
        self.detect_key_button.setEnabled(True)
        self.key_box.setCurrentText(key)
        self.scale_box.setCurrentText(scale)
        self.key_progress.setValue(100)
        self.key_progress.setFormat("KEY DETECTION COMPLETE")
        self.analysis_label.setText(
            f"suggested {key} {scale} · {float(confidence):.0%} scale fit · "
            f"{analysis.voiced_fraction:.0%} voiced frames"
        )

    def _key_failed(self, job_id: int, message: str):
        if job_id != self._key_job_id or self._key_cancel is None:
            return
        cancelled = self._key_cancel.is_set() or "cancel" in message.lower()
        self._key_cancel = None
        self.detect_key_button.setText("DETECT KEY")
        self.detect_key_button.setEnabled(True)
        self.key_progress.setValue(0)
        self.key_progress.setFormat(
            "KEY DETECTION CANCELLED" if cancelled else "KEY DETECTION FAILED"
        )
        self.analysis_label.setText(
            "key detection cancelled" if cancelled else f"key detection failed · {message}"
        )

    def _tune_progress(self, job_id: int, value: int):
        if job_id == self._tune_job_id and self._tune_cancel is not None:
            self.progress.setValue(value)

    def _tune_finished(self, job_id: int, audio, analysis, name: str):
        if job_id != self._tune_job_id or self._tune_cancel is None:
            return
        if self._tune_cancel.is_set():
            self._tune_failed(job_id, "vocal tuning cancelled")
            return
        self._tune_cancel = None
        self.render_button.setText("✦ TUNE → NEW TAKE")
        self.render_button.setEnabled(True)
        self.app.snapshot()
        try:
            source_id = self._render_source_id
            source = self.app.library.clips.get(source_id)
            parent = source.parent if source and source.kind == "vocal-tuned" else source_id
            clip = self.app.library.add_audio(audio, name, kind="vocal-tuned", parent=parent)
        except Exception as exc:
            if hasattr(self.app, "discard_snapshot"):
                self.app.discard_snapshot()
            self._tune_failed_after_save(str(exc))
            return
        self._latest_clip = clip.id
        self._render_source_id = None
        self.refresh_takes(select=clip.id)
        self.app._library_changed()
        voiced = analysis.confidence > 0.35
        median = (
            note_name(float(np.nanmedian(analysis.detected_midi[voiced])))
            if np.any(voiced)
            else "—"
        )
        self.progress.setValue(100)
        self.progress.setFormat("TUNED TAKE READY")
        self.analysis_label.setText(
            f"created {clip.name} · original preserved · "
            f"{analysis.voiced_fraction:.0%} voiced · median note {median}"
        )

    def _tune_failed_after_save(self, message: str):
        self._render_source_id = None
        self.render_button.setEnabled(True)
        self.progress.setValue(0)
        self.progress.setFormat("FAILED")
        self.analysis_label.setText(f"vocal processing failed · {message}")

    def _tune_failed(self, job_id: int, message: str):
        if job_id != self._tune_job_id or self._tune_cancel is None:
            return
        cancelled = self._tune_cancel.is_set() or "cancel" in message.lower()
        self._tune_cancel = None
        self._render_source_id = None
        self.render_button.setText("✦ TUNE → NEW TAKE")
        self.render_button.setEnabled(True)
        self.progress.setValue(0)
        self.progress.setFormat("TUNE CANCELLED" if cancelled else "FAILED")
        self.analysis_label.setText(
            "vocal tuning cancelled" if cancelled else f"vocal processing failed · {message}"
        )

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
