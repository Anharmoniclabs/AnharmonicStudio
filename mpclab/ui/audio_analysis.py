"""Cancelable, device-free analysis of a completed render or imported audio file."""

from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ..audio_analysis import AnalysisCancelled, analyze_audio_file, save_analysis_report
from ..workflow_commands import CommandSpec
from .window_client import WindowClient


class AudioAnalysisJob(QObject):
    """One bounded streaming read on a Python worker; no QThread/audio device."""

    progress = Signal(object, object)  # A long file can exceed Qt's 32-bit int range.
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, source, parent=None, *, analysis_function=analyze_audio_file):
        super().__init__(parent)
        self.source = source
        self._analyze = analysis_function
        self._cancel = threading.Event()
        self._thread = None
        # Parent destruction cancels work even if its window bypassed closeEvent.
        event = self._cancel
        self.destroyed.connect(lambda *_: event.set())

    def start(self):
        if self._thread is not None:
            raise RuntimeError("An analysis job can only be started once.")
        self._thread = threading.Thread(target=self._run, name="audio-file-analysis", daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    def _notify(self, name, *args):
        try:
            getattr(self, name).emit(*args)
        except RuntimeError:
            # The application may have destroyed the dialog while the reader
            # was inside libsndfile. Cancellation still closes its file handle.
            self._cancel.set()

    def _run(self):
        try:
            report = self._analyze(
                self.source,
                cancel=self._cancel,
                progress=lambda done, total: self._notify("progress", done, total),
            )
            if self._cancel.is_set():
                raise AnalysisCancelled()
            self._notify("succeeded", report)
        except AnalysisCancelled:
            self._notify("cancelled")
        except Exception as exc:
            self._notify("failed", str(exc) or type(exc).__name__)
        finally:
            self._notify("finished")


class AudioAnalysisDialog(QDialog):
    def __init__(self, parent=None, *, analysis_function=analyze_audio_file):
        super().__init__(parent)
        self.setWindowTitle("Analyze rendered audio")
        self.resize(820, 620)
        self._analysis_function = analysis_function
        self._job = None
        self._close_pending = False
        self.report = None
        layout = QVBoxLayout(self)
        description = QLabel(
            "Read-only file analysis: stored-sample peak, RMS, full-scale samples, "
            "DC offset and stereo correlation. No playback device is opened.\n"
            "LUFS, loudness range and true peak / dBTP are not measured."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        file_row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText("Choose a completed audio render or another audio file…")
        self.source.setAccessibleName("Audio file to analyze")
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self.choose_file)
        file_row.addWidget(self.source, 1)
        file_row.addWidget(self.browse_button)
        layout.addLayout(file_row)
        action_row = QHBoxLayout()
        self.analyze_button = QPushButton("Analyze file")
        self.analyze_button.clicked.connect(self.start_analysis)
        self.cancel_button = QPushButton("Cancel analysis")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        action_row.addWidget(self.analyze_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.progress, 1)
        layout.addLayout(action_row)
        self.status = QLabel("Choose a file to begin. The source audio is never modified.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setAccessibleName("Audio analysis report")
        layout.addWidget(self.results, 1)
        footer = QHBoxLayout()
        self.save_json_button = QPushButton("Save JSON report…")
        self.save_text_button = QPushButton("Save text report…")
        self.save_json_button.clicked.connect(lambda: self.choose_report_destination("json"))
        self.save_text_button.clicked.connect(lambda: self.choose_report_destination("text"))
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.save_json_button)
        footer.addWidget(self.save_text_button)
        footer.addStretch()
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self._set_busy(False)

    @property
    def busy(self):
        return self._job is not None

    def _set_busy(self, busy):
        for widget in (self.source, self.browse_button, self.analyze_button):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        for button in (self.save_json_button, self.save_text_button):
            button.setEnabled(not busy and self.report is not None)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose audio to analyze",
            self.source.text(),
            "Audio files (*.wav *.aif *.aiff *.flac *.ogg *.mp3 *.caf *.w64 *.rf64);;All files (*)",
        )
        if path:
            self.source.setText(path)

    @Slot()
    def start_analysis(self):
        if self.busy:
            return
        source = self.source.text().strip()
        if not source:
            self.status.setText("Choose an audio file first.")
            return
        self.report = None
        self.results.clear()
        self.progress.setValue(0)
        self.status.setText("Analyzing stored samples…")
        self._close_pending = False
        self._job = AudioAnalysisJob(source, self, analysis_function=self._analysis_function)
        self._job.progress.connect(self._progress)
        self._job.succeeded.connect(self._succeeded)
        self._job.failed.connect(self._failed)
        self._job.cancelled.connect(self._cancelled)
        self._job.finished.connect(self._finished)
        self._set_busy(True)
        try:
            self._job.start()
        except Exception as exc:
            self._failed(str(exc) or type(exc).__name__)
            self._finished()

    @Slot(object, object)
    def _progress(self, done, total):
        self.progress.setValue(min(1000, int(done * 1000 / total)) if total else 0)

    @Slot(object)
    def _succeeded(self, report):
        # A cancel may arrive after the worker queued a successful result.
        if self._job is None or self._job._cancel.is_set():
            return
        self.report = report
        self.results.setPlainText(report.to_text())
        self.progress.setValue(1000)
        self.status.setText(
            f"Analysis complete — {report.duration_seconds:.3f} seconds, "
            f"{len(report.findings)} sample-domain observation(s). "
            "LUFS and true peak were not measured."
        )

    @Slot(str)
    def _failed(self, message):
        self.status.setText(f"Analysis failed: {message}")

    @Slot()
    def _cancelled(self):
        self.status.setText("Analysis cancelled. No report was saved; source audio is unchanged.")

    @Slot()
    def _finished(self):
        job, self._job = self._job, None
        if job is not None:
            if job._cancel.is_set() and self.report is None:
                self._cancelled()
            job.deleteLater()
        self._set_busy(False)
        if self._close_pending:
            self._close_pending = False
            super().reject()

    @Slot()
    def cancel_analysis(self):
        if self._job is not None:
            self._job.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("Cancelling analysis…")

    def choose_report_destination(self, kind):
        if self.busy or self.report is None:
            return
        suffix = ".json" if kind == "json" else ".txt"
        default = str(Path(self.report.source).with_suffix(".analysis" + suffix))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save completed analysis report", default, f"Report (*{suffix})"
        )
        if not path:
            return
        try:
            # QFileDialog provides the overwrite confirmation. The backend
            # additionally refuses the original source or a hard link to it.
            saved = save_analysis_report(self.report, path, report_format=kind, overwrite=True)
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not save report: {exc}")
        else:
            self.status.setText(f"Report saved: {saved}")

    def reject(self):
        if self.busy:
            self._close_pending = True
            self.cancel_analysis()
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self._close_pending = True
            self.cancel_analysis()
            event.ignore()
            return
        super().closeEvent(event)


class AudioAnalysisController(WindowClient, QObject):
    def __init__(self, window, command_controller):
        super().__init__(window)
        self.app = window
        self.dialog = None
        command_controller.registry.register(
            CommandSpec(
                "audio.analyze_file",
                "Analyze rendered audio…",
                self.show_dialog,
                category="File",
                keywords=("render", "export", "peak", "RMS", "clipping", "DC", "correlation"),
            )
        )
        command_controller._reindex_bindings()
        # PySide may transfer ownership to a temporary QAction wrapper; keep
        # both wrappers alive for the controller's lifetime (including menus
        # that were created before this feature attached).
        self.menu_actions = window.menuBar().actions()
        file_menu = next(
            (
                action.menu()
                for action in self.menu_actions
                if action.text().replace("&", "").casefold() == "file" and action.menu()
            ),
            None,
        )
        if file_menu is None:
            file_menu = window.menuBar().addMenu("&File")
        self.file_menu = file_menu
        self.file_menu_action = file_menu.menuAction()
        self.action = file_menu.addAction("Analyze rendered audio…")
        self.action.triggered.connect(lambda: command_controller._execute("audio.analyze_file"))

    def show_dialog(self):
        if self.dialog is None:
            self.dialog = AudioAnalysisDialog(self.app)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        return self.dialog


def attach_audio_analysis(window, command_controller):
    existing = getattr(window, "audio_analysis_controller", None)
    if existing is not None:
        return existing
    controller = AudioAnalysisController(window, command_controller)
    window.audio_analysis_controller = controller
    return controller
