"""Track capture and its inspector, using the existing audio and note engines."""

from dataclasses import replace
from collections import deque
import math
import numpy as np

import numpy as np

from .window_client import WindowClient

from PySide6.QtCore import QObject, Signal, QEvent, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QProgressBar,
    QMessageBox,
    QScrollArea,
)

from ..model import Clip, Pattern, Row
from ..recording_timing import take_placement
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
        self.recorder.engine = app.engine
        self.armed_id = None
        self.target = None
        self.active = False
        self.pending = False
        self.unsaved = None
        self._capture_sample_rate = None
        self.notes = []
        self.midi_take = None
        self.held = {}
        self.peaks = deque(maxlen=4096)
        # Retain a truthful diagnostic after stopping.  A quiet take is still
        # the user's take; this is guidance, never a reason to delete it.
        self.take_peak = None
        self.message = "Select a track, choose its source, then arm and record."

    @property
    def busy(self):
        return self.active or self.pending or self.unsaved is not None or self.recovery_pending

    @property
    def recovery_pending(self):
        return not self.recorder.recording and self.recorder.temporary_path is not None

    @staticmethod
    def level_db(peak):
        """Return a bounded, user-facing dBFS value for an input peak."""
        if peak is None or peak <= 0:
            return None
        return 20.0 * math.log10(max(float(peak), 1e-8))

    def input_feedback(self, row=None):
        """Give the inspector a specific, non-destructive routing diagnosis."""
        row = row or self.target
        if row is None or row.record_source != "audio":
            return ""
        destination = (
            f"Mixer {row.record_track + 1} · {self.app.project.tracks[row.record_track].name}"
        )
        peak = self.recorder.input_peak if self.active else self.take_peak
        db = self.level_db(peak)
        if self.active:
            if peak is None or peak < 0.001:
                return f"NO INPUT · check Audio input setup → {destination}"
            if peak >= 0.98:
                return f"CLIPPING · lower Input gain → {destination}"
            return f"Input {db:.1f} dBFS → {destination}"
        if self.take_peak is not None and self.take_peak < 0.001:
            return "No signal was detected in this saved take · it was kept unchanged"
        if self.armed_id == row.id:
            return f"Ready · input will record to {destination}"
        return ""

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
        self.take_peak = None
        self.changed.emit()
        return True

    def start(self):
        if not self.pending:
            return
        self.pending = False
        app = self.app
        self._cue_error = ""
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
                from ..autotune.live import LiveMonitor, MonitorRoute

                route = MonitorRoute(self.recorder, app.engine, self.settings.monitor_gain)
                # Start dry capture first, then initialize using its negotiated rate.
                self.recorder.start(
                    device, self.settings.input_gain_db, route if self.settings.monitor else None
                )
                self.recorder.input_channels = tuple(self.settings.input_channels)
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
        app.engine.capture_anchor = None
        app.engine.capture_anchor_requested = self.target.record_source == "audio"
        if self.target.record_source == "notes":
            self.midi_take = app.engine.midi.begin_take(self.start_beat)
            app.engine.arp_note_capture = (self.notes, self.start_beat)
        app.engine.play()
        route = f"Mixer {self.target.record_track + 1} · {app.project.tracks[self.target.record_track].name}"
        self.message = f"Recording · {self.target.name} → {route} · Stop saves the take"
        app.status.showMessage(self.message)
        self.changed.emit()

    def note_on(self, pitch, velocity, pad=None, *, instrument=None, channel=0):
        if self.active and self.target.record_source == "notes":
            self.note_off(pitch, pad, instrument=instrument)
            key = (pitch, pad) if instrument is None else (pitch, pad, instrument)
            self.held[key] = (self.app.engine.beat, velocity, channel)

    def tick(self):
        if self.active and self.target.record_source == "audio":
            self.peaks.append((self.app.engine.beat, min(1.0, self.recorder.input_peak)))

    def note_off(self, pitch, pad=None, *, instrument=None):
        key = (pitch, pad) if instrument is None else (pitch, pad, instrument)
        held = self.held.pop(key, None)
        if held is not None:
            beat, velocity, channel = held
            self.notes.append(
                Note(
                    pitch,
                    max(0.0, beat - self.start_beat),
                    max(0.03125, self.app.engine.beat - beat),
                    velocity,
                    pad,
                    instrument=instrument,
                    channel=channel,
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
        if self.midi_take is not None:
            completed = self.app.engine.midi.end_take()
            if completed is not None:
                self.notes.extend(completed.notes)
                self.midi_take = completed
        self.app.engine.arp_note_capture = None
        for key in tuple(self.held):
            self.note_off(key[0], key[1], instrument=key[2] if len(key) > 2 else None)
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
        if audio is not None and len(audio):
            self.take_peak = float(np.max(np.abs(audio)))
        # Only discard when no frames were captured at all.  A very short or
        # silent performance is valuable evidence when debugging a route and
        # must remain available for the musician to inspect, edit, or retry.
        if (audio is not None and not len(audio)) or (audio is None and not self.notes):
            self.recorder.discard()
            self.message = "No take saved · the input device delivered no audio frames"
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
            additional_rows = []
            if audio is not None:
                split = self.settings.split_inputs or audio.shape[1] > 2
                sources = []
                for channel in range(len(self.settings.input_channels) if split else 1):
                    block = (
                        np.repeat(audio[:, channel : channel + 1], 2, axis=1) if split else audio
                    )
                    label = (
                        f"{name} · Input {self.settings.input_channels[channel] + 1}"
                        if split
                        else name
                    )
                    sources.append(app.library.add_audio(block, label, kind="recording"))
                for channel, source in enumerate(sources):
                    beat, trim, length = take_placement(
                        self.start_beat,
                        source.duration,
                        app.project.bpm,
                        self.settings.input_latency_ms,
                        first_capture=self.recorder.first_capture_monotonic,
                        anchor=app.engine.capture_anchor,
                    )
                    placed = Clip(
                        kind="audio",
                        ref=source.id,
                        start_beat=beat,
                        offset=trim,
                        length_beats=max(1 / app.engine.sr, length),
                        source_length=source.duration,
                        track=min(len(app.project.tracks) - 1, self.target.record_track + channel),
                    )
                    if channel == 0:
                        clip = placed
                    else:
                        additional_rows.append(
                            Row(
                                name=f"{self.target.name} · Input {self.settings.input_channels[channel] + 1}",
                                clips=[placed],
                                record_track=placed.track,
                            )
                        )
            else:
                length = max(n.start + n.duration for n in notes)
                pattern = Pattern(name=name, bars=max(1, math.ceil(length / 4)))
                pattern.notes = notes
                if self.midi_take is not None:
                    pattern.midi_controls = list(self.midi_take.controls)
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
            position = app.project.rows.index(row) + 1
            app.project.rows[position:position] = additional_rows
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
        self.midi_take = None
        self.target = None
        app._library_changed()
        app._refresh_place_box()
        app.playlist.refresh()
        app.playlist.select_clip(clip)
        self.message = f"Saved · {name}"
        if audio is not None and self.take_peak is not None and self.take_peak < 0.001:
            self.message += " · no input detected (take kept unchanged)"
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
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(4)
        self.title = QLabel("Track inspector")
        self.title.setObjectName("title")
        self.title.hide()
        primary = QHBoxLayout()
        primary.setSpacing(6)
        layout.addLayout(primary)
        self.name = QPushButton("Select a track")
        self.name.setMaximumWidth(180)
        self.name.clicked.connect(self.rename)
        primary.addWidget(self.name)
        self.details = QWidget()
        form = QFormLayout(self.details)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.source = QComboBox()
        self.source.addItem("Audio input", "audio")
        self.source.addItem("Instrument / sample", "notes")
        self.source.currentIndexChanged.connect(self.source_changed)
        primary.addWidget(QLabel("Source"))
        primary.addWidget(self.source)
        self.output = QComboBox()
        for index, track in enumerate(app.project.tracks):
            self.output.addItem(f"{index + 1} · {track.name}", index)
        self.output.currentIndexChanged.connect(self.output_changed)
        primary.addWidget(QLabel("To"))
        primary.addWidget(self.output)
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
        self.corrected_monitor = QCheckBox("Pitch-corrected cue · V2")
        self.corrected_monitor.setToolTip(
            "Uses the Vocal pitch settings. Adds processing delay; recording stays dry."
        )
        self.corrected_monitor.toggled.connect(self.settings_changed)
        form.addRow(self.corrected_monitor)

        self.arm = QPushButton("Arm track")
        self.arm.setCheckable(True)
        self.arm.setObjectName("rec")
        self.arm.clicked.connect(self.toggle_arm)
        primary.addWidget(self.arm)
        self.details_button = QPushButton("Input settings ▾")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self.details.setVisible)
        primary.addWidget(self.details_button)
        primary.addStretch(1)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setFormat("Input %p%")
        self.meter.setValue(0)
        form.addRow(self.meter)
        layout.addWidget(self.details)
        self.details.hide()
        self.input_feedback = QLabel()
        self.input_feedback.setObjectName("inputFeedback")
        self.input_feedback.setWordWrap(True)
        layout.addWidget(self.input_feedback)
        self.state = QLabel()
        self.state.setWordWrap(True)
        secondary = QHBoxLayout()
        layout.addLayout(secondary)
        secondary.addWidget(self.state, 1)
        self.retry = QPushButton("Retry save take")
        self.retry.clicked.connect(app.track_capture.save_take)
        secondary.addWidget(self.retry)
        self.discard = QPushButton("Discard unsaved take…")
        self.discard.clicked.connect(app.track_capture.discard_take)
        secondary.addWidget(self.discard)
        self.notes_help = QLabel(
            "Play the selected instrument or sample using Notes or musical typing. Synth notes share the current instrument sound."
        )
        self.notes_help.setWordWrap(True)
        form.addRow(self.notes_help)
        self.record_song = QPushButton("Record in Song")
        self.record_song.setObjectName("rec")
        self.record_song.clicked.connect(self.record_in_song)
        secondary.addWidget(self.record_song)
        self.tune_clip = QPushButton("Open selected clip in Autotune")
        self.tune_clip.clicked.connect(lambda: app.open_vocal_clip())
        secondary.addWidget(self.tune_clip)
        app.track_capture.changed.connect(self.sync)
        self.sync()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.LayoutRequest:
            viewport = self.parentWidget()
            scroll = viewport.parentWidget() if viewport else None
            if isinstance(scroll, QScrollArea):
                self.setMinimumWidth(self.sizeHint().width())
                scroll.setFixedHeight(min(300, self.sizeHint().height() + 11))
                scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return result

    def row(self):
        return next((r for r in self.app.project.rows if r.id == self.row_id), None)

    def select_row(self, row):
        self.row_id = row.id
        self.app.track_controls_button.setChecked(True)
        self.sync()

    def rename(self):
        if self.row() is not None:
            self.app.rename_song_row(self.row())
            self.sync()

    def toggle_arm(self):
        if self.row() is not None:
            self.app.track_capture.arm(self.row())
        self.sync()

    def record_in_song(self):
        self.app.show_tab(2)
        if self.app.track_capture.active or self.app.track_capture.pending:
            self.app.stop_all()
            return
        row = self.row()
        if row is None:
            return
        if self.app.track_capture.armed_id != row.id:
            self.app.track_capture.arm(row)
        self.app.btn_rec.setChecked(True)

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
        values = (
            self.count.currentData(),
            self.gain.value(),
            self.monitor.isChecked(),
            self.corrected_monitor.isChecked(),
        )
        if values != (rec.count_in_bars, rec.input_gain_db, rec.monitor, rec.corrected_monitor):
            self.app.snapshot()
            rec.count_in_bars, rec.input_gain_db, rec.monitor, rec.corrected_monitor = values
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
            (self.corrected_monitor, rec.corrected_monitor),
            (self.arm, bool(row and capture.armed_id == row.id)),
        ):
            widget.blockSignals(True)
            if isinstance(widget, QDoubleSpinBox):
                widget.setValue(value)
            else:
                widget.setChecked(value)
            widget.blockSignals(False)
        self.arm.setText("Armed · press Record" if self.arm.isChecked() else "Arm track")
        self.record_song.setText(
            "Stop & save take" if capture.active or capture.pending else "Record in Song"
        )
        self.record_song.setEnabled(
            bool(row) and not (capture.unsaved is not None or capture.recovery_pending)
        )
        self.state.setText(capture.message)
        self.update_input_feedback()
        self.retry.setVisible(capture.unsaved is not None or capture.recovery_pending)
        self.discard.setVisible(capture.unsaved is not None or capture.recovery_pending)
        self.notes_help.setVisible(bool(row and row.record_source == "notes"))
        self.output.setEnabled(bool(row and row.record_source == "audio" and not capture.busy))
        for widget in (
            self.count,
            self.gain,
            self.monitor,
            self.corrected_monitor,
            self.input_button,
        ):
            widget.setEnabled(not capture.busy)

    def update_input_feedback(self):
        """Refresh live level/routing guidance without changing capture state."""
        text = self.app.track_capture.input_feedback(self.row())
        live = getattr(self.app.track_capture, "_live_monitor", None)
        if live is not None:
            text += " · " + (
                live.error or f"Corrected cue: ~{live.latency_ms:.0f} ms processing delay"
            )
        elif self.app.track_capture.active and getattr(self.app.track_capture, "_cue_error", ""):
            text += " · " + self.app.track_capture._cue_error
        self.input_feedback.setText(text)
        self.input_feedback.setVisible(bool(text))
        peak = self.app.track_capture.recorder.input_peak
        db = self.app.track_capture.level_db(peak)
        if self.app.track_capture.active and db is not None:
            self.meter.setFormat(f"Input {db:.1f} dBFS")
        elif self.app.track_capture.active:
            self.meter.setFormat("Input — dBFS")
        else:
            self.meter.setFormat("Input %p%")
