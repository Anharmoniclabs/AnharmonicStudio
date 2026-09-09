"""Track capture and its inspector, using the existing audio and note engines."""

from dataclasses import replace
from collections import deque
import math

from .window_client import WindowClient

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QProgressBar,
    QMessageBox,
)

from ..model import Clip, Pattern
from ..music import Note
from ..vocal import VocalRecorder, input_device_inventory


class TrackCapture(WindowClient, QObject):
    changed = Signal()

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.recorder = VocalRecorder(
            app.engine.sr, app.engine.blocksize, temp_dir=app.root / "projects" / "recordings"
        )
        self.armed_id = None
        self.target = None
        self.active = False
        self.pending = False
        self.unsaved = None
        self.notes = []
        self.held = {}
        self.peaks = deque(maxlen=4096)
        self.message = "Select a track, choose its source, then arm and record."

    @property
    def busy(self):
        return self.active or self.pending or self.unsaved is not None or self.recovery_pending

    @property
    def recovery_pending(self):
        return not self.recorder.recording and self.recorder.temporary_path is not None

    def arm(self, row):
        if self.busy:
            self.app.status.showMessage(
                "Stop the current take before changing its destination", 3500
            )
            return
        self.armed_id = None if self.armed_id == row.id else row.id
        self.message = f"Armed · {row.name}" if self.armed_id else "Track disarmed"
        self.changed.emit()
        self.app.playlist.update()

    def prepare(self):
        row = next((r for r in self.app.project.rows if r.id == self.armed_id), None)
        vocal = self.app.vocal_panel
        if (
            row is None
            or self.busy
            or (vocal.recorder.recording or vocal.recorder.temporary_path is not None)
            or vocal._counting
        ):
            self.app.status.showMessage("Finish the current take before starting another", 3500)
            return False
        self.target = replace(row, clips=[])
        self.project = self.app.project
        self.settings = replace(self.project.vocal_record)
        self.start_beat = float(self.app.engine.beat)
        self.pending = True
        self.notes, self.held = [], {}
        self.peaks.clear()
        self.changed.emit()
        return True

    def start(self):
        if not self.pending:
            return
        self.pending = False
        app = self.app
        try:
            if self.target.record_source == "audio":
                device = None
                if self.settings.input_device:
                    inputs, _ = input_device_inventory()
                    device = next(
                        (i["index"] for i in inputs if i["key"] == self.settings.input_device), None
                    )
                    if device is None:
                        raise RuntimeError(
                            "Selected input is unavailable. Choose an input in Audio setup."
                        )
                self.recorder.sample_rate = app.engine.sr
                self.recorder.blocksize = app.engine.blocksize
                monitor = (
                    (lambda block: app.engine.queue_monitor(block, self.settings.monitor_gain))
                    if self.settings.monitor
                    else None
                )
                self.recorder.start(device, self.settings.input_gain_db, monitor)
        except Exception as exc:
            self.message = f"Input could not start · {exc}"
            self.target = None
            app.status.showMessage(self.message, 8000)
            self.changed.emit()
            return
        self.active = True
        self.previous_loop = app.engine.loop_song
        app.engine.loop_song = False
        app.engine.set_position(self.start_beat)
        app.engine.play()
        self.message = f"Recording · {self.target.name} · Stop saves the take"
        app.status.showMessage(self.message)
        self.changed.emit()

    def note_on(self, pitch, velocity, pad=None):
        if self.active and self.target.record_source == "notes":
            self.note_off(pitch, pad)
            self.held[(pitch, pad)] = (self.app.engine.beat, velocity)

    def tick(self):
        if self.active and self.target.record_source == "audio":
            self.peaks.append((self.app.engine.beat, min(1.0, self.recorder.input_peak)))

    def note_off(self, pitch, pad=None):
        held = self.held.pop((pitch, pad), None)
        if held is not None:
            beat, velocity = held
            self.notes.append(
                Note(
                    pitch,
                    max(0.0, beat - self.start_beat),
                    max(0.03125, self.app.engine.beat - beat),
                    velocity,
                    pad,
                )
            )

    def finish(self):
        if self.pending:
            self.pending = False
            self.target = None
            self.message = "Count-in cancelled"
            self.changed.emit()
            return
        if not self.active:
            return
        for pitch, pad in tuple(self.held):
            self.note_off(pitch, pad)
        self.active = False
        self.app.engine.loop_song = self.previous_loop
        self.app.engine.stop_transport(rewind=False)
        try:
            audio = self.recorder.stop() if self.target.record_source == "audio" else None
        except Exception as exc:
            self.message = f"Capture failed · {exc}"
            self.changed.emit()
            self.app.status.showMessage(self.message, 8000)
            return
        if (audio is not None and len(audio) < self.app.engine.sr * 0.08) or (
            audio is None and not self.notes
        ):
            self.recorder.discard()
            self.message = "No take saved · no notes or less than 80 ms of audio"
            self.target = None
            self.changed.emit()
            return
        self.unsaved = (audio, list(self.notes))
        self.save_take()

    def save_take(self):
        if self.unsaved is None and self.recovery_pending:
            try:
                self.unsaved = (self.recorder.stop(), list(self.notes))
            except Exception as exc:
                self.message = f"Take recovery needs attention · {exc}"
                self.changed.emit()
                return
        if self.unsaved is None:
            return
        app = self.app
        if app.project is not self.project:
            self.message = "Take retained for retry · return to the original project to save"
            self.changed.emit()
            return
        row = next((r for r in app.project.rows if r.id == self.target.id), None)
        if row is None:
            self.message = "Take retained for retry · restore its destination track to save"
            self.changed.emit()
            return
        audio, notes = self.unsaved
        previous_redo = list(app._redo)
        was_dirty = app._dirty
        app.snapshot()
        try:
            name = f"{self.target.name} · Take {len(row.clips) + 1}"
            if audio is not None:
                source = app.library.add_audio(audio, name, kind="recording")
                length = source.duration * app.project.bpm / 60.0
                latency = self.settings.input_latency_ms * app.project.bpm / 60000.0
                clip = Clip(
                    kind="audio",
                    ref=source.id,
                    start_beat=max(0.0, self.start_beat - latency),
                    length_beats=length,
                    source_length=source.duration,
                    track=self.target.record_track,
                )
            else:
                length = max(n.start + n.duration for n in notes)
                pattern = Pattern(name=name, bars=max(1, math.ceil(length / 4)))
                pattern.notes = notes
                app.project.patterns.append(pattern)
                clip = Clip(
                    kind="pattern",
                    ref=pattern.id,
                    start_beat=self.start_beat,
                    length_beats=pattern.length_beats,
                )
                app.project.current_pattern = pattern.id
                app._sync_pattern_controls()
            row.clips.append(clip)
        except Exception as exc:
            app.discard_snapshot()
            app._redo[:] = previous_redo
            app._set_dirty(was_dirty)
            app._try_save_history()
            self.message = f"Take retained for retry · save failed: {exc} · Retry save"
            self.changed.emit()
            return
        self.recorder.commit()
        self.unsaved = None
        self.target = None
        app._library_changed()
        app._refresh_place_box()
        app.playlist.refresh()
        app.playlist.select_clip(clip)
        self.message = f"Saved · {name}"
        if self.recorder.dropped_frames:
            self.message += f" · {1000 * self.recorder.dropped_frames / self.recorder.sample_rate:.1f} ms dropout filled with silence"
        app.status.showMessage(self.message, 5000)
        self.changed.emit()

    def discard_take(self):
        if self.active or self.pending:
            return
        if (
            QMessageBox.question(
                self.app,
                "Discard unsaved take?",
                "Remove this unsaved recording? This cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        try:
            self.recorder.discard()
        except Exception as exc:
            self.message = f"Could not discard take · {exc}"
            self.changed.emit()
            return
        self.unsaved = None
        self.target = None
        self.notes.clear()
        self.peaks.clear()
        self.message = "Unsaved take discarded"
        self.changed.emit()


class TrackInspector(WindowClient, QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.row_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(12)
        self.title = QLabel("Track inspector")
        self.title.setObjectName("title")
        layout.addWidget(self.title)
        self.name = QPushButton("Select a track")
        self.name.clicked.connect(self.rename)
        layout.addWidget(self.name)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.source = QComboBox()
        self.source.addItem("Audio input · mic / instrument", "audio")
        self.source.addItem("Instrument / sample notes", "notes")
        self.source.currentIndexChanged.connect(self.source_changed)
        form.addRow("Record", self.source)
        self.output = QComboBox()
        for index, track in enumerate(app.project.tracks):
            self.output.addItem(f"{index + 1} · {track.name}", index)
        self.output.currentIndexChanged.connect(self.output_changed)
        form.addRow("Audio to", self.output)
        self.input_button = QPushButton("Audio input setup…")
        self.input_button.clicked.connect(app.show_audio_setup)
        form.addRow("Input", self.input_button)
        self.count = QComboBox()
        for beats in (0, 1, 2, 3, 4):
            self.count.addItem("Off" if beats == 0 else f"{beats} bar(s)", beats)
        self.count.currentIndexChanged.connect(self.settings_changed)
        form.addRow("Count-in", self.count)
        self.gain = QDoubleSpinBox()
        self.gain.setRange(-24, 24)
        self.gain.setSuffix(" dB")
        self.gain.setKeyboardTracking(False)
        self.gain.valueChanged.connect(self.settings_changed)
        form.addRow("Input gain", self.gain)
        self.monitor = QCheckBox("Monitor input")
        self.monitor.setToolTip("Listen to the input while recording. Use headphones.")
        self.monitor.toggled.connect(self.settings_changed)
        form.addRow(self.monitor)
        layout.addLayout(form)
        self.arm = QPushButton("Arm track")
        self.arm.setCheckable(True)
        self.arm.setObjectName("rec")
        self.arm.clicked.connect(self.toggle_arm)
        layout.addWidget(self.arm)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setFormat("Input %p%")
        self.meter.setValue(0)
        layout.addWidget(self.meter)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.retry = QPushButton("Retry save take")
        self.retry.clicked.connect(app.track_capture.save_take)
        layout.addWidget(self.retry)
        self.discard = QPushButton("Discard unsaved take…")
        self.discard.clicked.connect(app.track_capture.discard_take)
        layout.addWidget(self.discard)
        self.notes_help = QLabel(
            "Play the selected instrument or sample using Notes or musical typing. Synth notes share the current instrument sound."
        )
        self.notes_help.setWordWrap(True)
        layout.addWidget(self.notes_help)
        takes = QPushButton("Takes, comp & tuning…")
        takes.clicked.connect(lambda: app.show_tab(5))
        layout.addWidget(takes)
        layout.addStretch(1)
        app.track_capture.changed.connect(self.sync)
        self.sync()

    def row(self):
        return next((r for r in self.app.project.rows if r.id == self.row_id), None)

    def select_row(self, row):
        self.row_id = row.id
        self.app.side_pages.setCurrentIndex(0)
        self.sync()

    def rename(self):
        if self.row() is not None:
            self.app.rename_song_row(self.row())
            self.sync()

    def toggle_arm(self):
        if self.row() is not None:
            self.app.track_capture.arm(self.row())
        self.sync()

    def source_changed(self):
        row = self.row()
        if row is not None and row.record_source != self.source.currentData():
            self.app.snapshot()
            row.record_source = self.source.currentData()
            self.sync()

    def output_changed(self):
        row = self.row()
        if row is not None and row.record_track != self.output.currentData():
            self.app.snapshot()
            row.record_track = self.output.currentData()

    def settings_changed(self):
        rec = self.app.project.vocal_record
        values = (self.count.currentData(), self.gain.value(), self.monitor.isChecked())
        if values != (rec.count_in_bars, rec.input_gain_db, rec.monitor):
            self.app.snapshot()
            rec.count_in_bars, rec.input_gain_db, rec.monitor = values
            self.app.vocal_panel.sync()

    def sync(self):
        capture = self.app.track_capture
        row = self.row()
        self.name.setText(row.name if row else "Select a track")
        for widget in (self.source, self.output, self.arm, self.name):
            widget.setEnabled(row is not None and not capture.busy)
        rec = self.app.project.vocal_record
        for widget, value in (
            (self.source, self.source.findData(row.record_source if row else "audio")),
            (self.output, row.record_track if row else 3),
            (self.count, rec.count_in_bars),
        ):
            widget.blockSignals(True)
            widget.setCurrentIndex(value)
            widget.blockSignals(False)
        for widget, value in (
            (self.gain, rec.input_gain_db),
            (self.monitor, rec.monitor),
            (self.arm, bool(row and capture.armed_id == row.id)),
        ):
            widget.blockSignals(True)
            if isinstance(widget, QDoubleSpinBox):
                widget.setValue(value)
            else:
                widget.setChecked(value)
            widget.blockSignals(False)
        self.arm.setText("Armed · press Record" if self.arm.isChecked() else "Arm track")
        self.state.setText(capture.message)
        self.retry.setVisible(capture.unsaved is not None or capture.recovery_pending)
        self.discard.setVisible(capture.unsaved is not None or capture.recovery_pending)
        self.notes_help.setVisible(bool(row and row.record_source == "notes"))
        self.output.setEnabled(bool(row and row.record_source == "audio" and not capture.busy))
        for widget in (self.count, self.gain, self.monitor, self.input_button):
            widget.setEnabled(not capture.busy)
