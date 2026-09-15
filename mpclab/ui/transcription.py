"""Background song-to-score conversion and review before project insertion."""

from dataclasses import replace
from pathlib import Path
import tempfile
import threading

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QFileDialog,
    QScrollArea,
    QAbstractItemView,
)

from ..transcription import transcribe_file, TranscriptionCancelled
from ..transcription_project import apply_transcription
from ..scoring import Score, Part, ScoreNote, musicxml
from .scoring import qt_score_svg


class TranscriptionJob(QObject):
    progress = Signal(str, float)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self, source, settings, parent=None, *, song=None, library=None, convert=transcribe_file
    ):
        super().__init__(parent)
        self.source, self.settings = source, settings
        self.song, self.library = song, library
        self.convert = convert
        self.cancel_event = threading.Event()
        self.thread = None
        event = self.cancel_event
        self.destroyed.connect(lambda *_: event.set())

    def start(self):
        if self.thread is not None:
            raise RuntimeError("This conversion has already started.")
        self.thread = threading.Thread(target=self.run, name="song-to-score", daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancel_event.set()

    def notify(self, name, *args):
        try:
            getattr(self, name).emit(*args)
        except RuntimeError:
            self.cancel_event.set()

    def run(self):
        try:
            with tempfile.TemporaryDirectory(prefix="score-render-") as temporary:
                source = self.source
                if self.song is not None:
                    from ..export import render_export, ExportCancelled

                    source = Path(temporary) / "Song.wav"
                    try:
                        render_export(
                            self.song,
                            self.library,
                            source,
                            tail=0,
                            cancel=self.cancel_event,
                            progress=lambda value: self.notify(
                                "progress", "Rendering current song", value * 0.15
                            ),
                        )
                    except ExportCancelled as exc:
                        raise TranscriptionCancelled() from exc
                result = self.convert(
                    source,
                    **self.settings,
                    cancel=self.cancel_event,
                    progress=lambda message, value: self.notify(
                        "progress", message, 0.15 + 0.85 * value
                    ),
                )
                if self.cancel_event.is_set():
                    raise TranscriptionCancelled()
                if self.song is not None:
                    result.title = self.song.name
                self.notify("succeeded", result)
        except TranscriptionCancelled:
            self.notify("failed", "Conversion cancelled. No score was added.")
        except Exception as exc:
            self.notify("failed", str(exc) or type(exc).__name__)
        finally:
            self.notify("finished")


class TranscriptionDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.job = None
        self.result = None
        self.source_project = None
        self.close_pending = False
        self.setWindowTitle("Convert audio to score")
        self.resize(980, 840)
        layout = QVBoxLayout(self)
        description = QLabel(
            "Turn a song into editable instrument parts. The local note model is included; "
            "song modes use the optional stem engine and may download separation weights on first use. "
            "Review estimated notes before adding them."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        sources = QHBoxLayout()
        self.source_kind = QComboBox()
        self.source_kind.addItems(["Audio file", "Selected library audio", "Current song"])
        self.source_kind.setAccessibleName("Audio source")
        self.path = QLineEdit()
        self.path.setPlaceholderText("Choose a song or instrument recording…")
        self.path.setAccessibleName("Source audio file")
        self.browse = QPushButton("Browse…")
        self.browse.clicked.connect(self.choose_file)
        sources.addWidget(self.source_kind)
        sources.addWidget(self.path, 1)
        sources.addWidget(self.browse)
        layout.addLayout(sources)
        options = QHBoxLayout()
        self.mode = QComboBox()
        for label, value in (
            ("Song · vocals, bass, drums, other", "song4"),
            ("Song · also separate guitar and piano", "song6"),
            ("Single instrument · no separation", "single"),
        ):
            self.mode.addItem(label, value)
        self.mode.setAccessibleName("Transcription mode")
        self.auto_tempo = QCheckBox("Detect tempo")
        self.auto_tempo.setChecked(True)
        self.bpm = QDoubleSpinBox()
        self.bpm.setRange(40, 240)
        self.bpm.setValue(window.project.bpm)
        self.bpm.setSuffix(" BPM")
        self.bpm.setAccessibleName("Score tempo")
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.1, 0.9)
        self.threshold.setSingleStep(0.05)
        self.threshold.setValue(0.45)
        self.threshold.setToolTip("Higher thresholds keep fewer, more confident notes.")
        self.threshold.setAccessibleName("Note confidence threshold")
        for widget in (self.mode, self.auto_tempo, self.bpm, QLabel("Threshold"), self.threshold):
            options.addWidget(widget)
        layout.addLayout(options)
        actions = QHBoxLayout()
        self.convert = QPushButton("Convert to score")
        self.cancel = QPushButton("Cancel conversion")
        self.cancel.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        actions.addWidget(self.convert)
        actions.addWidget(self.cancel)
        actions.addWidget(self.progress, 1)
        layout.addLayout(actions)
        self.status = QLabel(
            "Choose a source. Scores use a fixed tempo, 4/4 and a sixteenth-note grid."
        )
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.parts = QTableWidget(0, 4)
        self.parts.setHorizontalHeaderLabels(
            ["Include / instrument name", "Notes", "Pitch range", "Mean confidence"]
        )
        self.parts.horizontalHeader().setStretchLastSection(True)
        self.parts.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.parts.setMaximumHeight(210)
        self.parts.setColumnWidth(0, 330)
        layout.addWidget(self.parts)
        self.preview_scroll = QScrollArea()
        self.preview = QSvgWidget()
        self.preview.setStyleSheet("background: white;")
        self.preview.setAccessibleName("Transcription preview, first four bars")
        self.preview_scroll.setWidget(self.preview)
        self.preview_scroll.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.preview_scroll, 1)
        placement = QHBoxLayout()
        self.use_tempo = QCheckBox("Use score tempo for the project")
        self.use_tempo.setChecked(not any(row.clips for row in window.project.rows))
        self.start_bar = QSpinBox()
        self.start_bar.setRange(1, 2048)
        self.start_bar.setAccessibleName("Score starting bar")
        placement.addWidget(self.use_tempo)
        placement.addStretch()
        placement.addWidget(QLabel("Add at bar"))
        placement.addWidget(self.start_bar)
        layout.addLayout(placement)
        note = QLabel(
            "New score rows start muted. Unmute them in Song to audition synthesized parts. "
            "Drum families and mixed-instrument stems may need correction."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        footer = QHBoxLayout()
        footer.addStretch()
        self.add = QPushButton("Add selected parts to project")
        self.add.setEnabled(False)
        self.close_button = QPushButton("Close")
        footer.addWidget(self.add)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.source_kind.currentIndexChanged.connect(self.source_changed)
        self.convert.clicked.connect(self.start_conversion)
        self.cancel.clicked.connect(self.cancel_conversion)
        self.close_button.clicked.connect(self.reject)
        self.add.clicked.connect(self.add_parts)
        self.parts.itemChanged.connect(lambda *_: self.render_preview())
        self.bpm.valueChanged.connect(lambda *_: self.render_preview())
        self.use_tempo.toggled.connect(lambda *_: self.render_preview())
        self.source_changed()

    def source_changed(self, *_):
        file_source = self.source_kind.currentIndex() == 0
        self.path.setEnabled(file_source)
        self.browse.setEnabled(file_source)
        if self.source_kind.currentIndex() == 1:
            clip = getattr(self.window, "current_clip", None)
            self.path.setText(str(self.window.library.wav_path(clip)) if clip else "")
        elif self.source_kind.currentIndex() == 2:
            self.path.setText(self.window.project.name)
            self.auto_tempo.setChecked(False)
            self.bpm.setValue(self.window.project.bpm)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose audio to transcribe",
            self.path.text(),
            "Audio (*.wav *.mp3 *.flac *.ogg *.aif *.aiff *.m4a);;All files (*)",
        )
        if path:
            self.path.setText(path)

    def set_busy(self, busy):
        for widget in (
            self.convert,
            self.source_kind,
            self.mode,
            self.auto_tempo,
            self.bpm,
            self.threshold,
            self.path,
            self.browse,
        ):
            widget.setEnabled(not busy)
        self.cancel.setEnabled(busy)
        self.add.setEnabled(not busy and self.result is not None and self.result.note_count > 0)
        if not busy:
            # Avoid replacing the source path or detected tempo after a job.
            file_source = self.source_kind.currentIndex() == 0
            self.path.setEnabled(file_source)
            self.browse.setEnabled(file_source)

    def start_conversion(self):
        if self.job is not None:
            return
        source = self.path.text().strip()
        if self.source_kind.currentIndex() != 2 and not Path(source).is_file():
            self.status.setText("Choose an existing audio file or select library audio first.")
            return
        from ..model import Project
        from ..export import library_snapshot

        song = None
        if self.source_kind.currentIndex() == 2:
            if not any(row.clips for row in self.window.project.rows):
                self.status.setText("Arrange some audio or patterns in Song first.")
                return
            song = Project.from_dict(self.window.project.to_dict())
            if song.song_length_beats * 60 / song.bpm > 900:
                self.status.setText(
                    "Convert songs up to 15 minutes long. Shorten the arrangement first."
                )
                return
        self.source_project = self.window.project
        self.result = None
        self.parts.setRowCount(0)
        self.preview.hide()
        self.progress.setValue(0)
        self.set_busy(True)
        self.status.setText("Starting local audio transcription…")
        settings = {
            "mode": self.mode.currentData(),
            "threshold": self.threshold.value(),
            "bpm": None if self.auto_tempo.isChecked() else self.bpm.value(),
        }
        self.job = TranscriptionJob(
            source,
            settings,
            self,
            song=song,
            library=library_snapshot(self.window.library) if song is not None else None,
        )
        self.job.progress.connect(self.report_progress)
        self.job.succeeded.connect(self.completed)
        self.job.failed.connect(self.status.setText)
        self.job.finished.connect(self.finished_job)
        self.job.start()

    def report_progress(self, message, value):
        self.status.setText(message)
        self.progress.setValue(round(value * 1000))

    def cancel_conversion(self):
        if self.job is not None:
            self.job.cancel()
            self.status.setText("Cancelling after the current model window…")
            self.cancel.setEnabled(False)

    def completed(self, result):
        if self.close_pending or (self.job is not None and self.job.cancel_event.is_set()):
            return
        self.result = result
        self.bpm.setValue(result.bpm)
        self.parts.blockSignals(True)
        self.parts.setRowCount(len(result.parts))
        for row, part in enumerate(result.parts):
            name = QTableWidgetItem(part.name)
            name.setFlags(name.flags() | Qt.ItemIsUserCheckable)
            name.setCheckState(Qt.Checked)
            self.parts.setItem(row, 0, name)
            pitches = [note.pitch for note in part.notes]
            values = [
                str(len(pitches)),
                "Percussion" if part.percussion else f"MIDI {min(pitches)}–{max(pitches)}",
                f"{sum(note.confidence for note in part.notes) / len(pitches):.0%}",
            ]
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.parts.setItem(row, column, item)
        self.parts.blockSignals(False)
        self.status.setText(
            f"{result.note_count} notes in {len(result.parts)} parts · {result.bpm:g} BPM. "
            "Review the first four bars below; change the tempo or part names as needed."
            if result.note_count
            else "No notes detected. Try a lower threshold or Single instrument mode."
        )
        self.render_preview()

    def selected_parts(self):
        if self.result is None:
            return []
        return [
            replace(part, name=self.parts.item(row, 0).text().strip())
            for row, part in enumerate(self.result.parts)
            if self.parts.item(row, 0) and self.parts.item(row, 0).checkState() == Qt.Checked
        ]

    def render_preview(self):
        if self.result is None or self.parts.rowCount() != len(self.result.parts):
            return
        parts = []
        bpm = self.bpm.value() if self.use_tempo.isChecked() else self.window.project.bpm
        for index, part in enumerate(self.selected_parts()):
            notes = []
            for note in part.notes:
                start = max(0, round(note.start * bpm / 60 * 4))
                if start < 64:
                    end = min(64, max(start + 1, round(note.end * bpm / 60 * 4)))
                    notes.append(ScoreNote(note.pitch, start, end - start))
            parts.append(Part(str(index), part.name, part.percussion, notes))
        self.add.setEnabled(self.job is None and bool(parts))
        if not parts:
            self.preview.hide()
            return
        import verovio

        toolkit = verovio.toolkit()
        toolkit.setOptions(
            {"pageWidth": 2100, "pageHeight": 2970, "scale": 40, "adjustPageHeight": True}
        )
        if toolkit.loadData(musicxml(Score(self.result.title, bpm, 4, parts))):
            self.preview.load(qt_score_svg(toolkit.renderToSVG(1)))
            self.preview.resize(self.preview.renderer().defaultSize())
            self.preview.show()

    def finished_job(self):
        if self.job is not None:
            self.job.deleteLater()
        self.job = None
        self.set_busy(False)
        if self.close_pending:
            super().reject()

    def add_parts(self):
        if self.result is None or self.job is not None:
            return
        if self.window.project is not self.source_project:
            self.status.setText(
                "The project changed during conversion. Convert again for the current project."
            )
            return
        try:
            apply_transcription(
                self.window,
                self.result,
                parts=self.selected_parts(),
                bpm=self.bpm.value() if self.use_tempo.isChecked() else self.window.project.bpm,
                start_beat=(self.start_bar.value() - 1) * 4,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.window.scoring_panel.scope.setCurrentIndex(1)
        self.window.scoring_panel.refresh()
        self.window.show_tab(9)
        self.accept()

    def reject(self):
        if self.job is not None:
            self.close_pending = True
            self.cancel_conversion()
            return
        super().reject()

    def closeEvent(self, event):
        if self.job is not None:
            self.close_pending = True
            self.cancel_conversion()
            event.ignore()
        else:
            super().closeEvent(event)
