"""File loudness and normalized delivery using the shared cancellable worker."""

from functools import partial
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog, QLabel

from ..audio_normalization import normalize_audio_file
from ..loudness import measure_loudness
from ..workflow_commands import CommandSpec
from .audio_analysis import AudioAnalysisDialog


class LoudnessDialog(AudioAnalysisDialog):
    def __init__(self, window, *, destination=None, target=-14.0, ceiling=-1.0):
        function = (
            measure_loudness
            if destination is None
            else partial(
                normalize_audio_file,
                destination=destination,
                target_lufs=target,
                true_peak_ceiling=ceiling,
            )
        )
        super().__init__(window, analysis_function=function)
        self.normalizing = destination is not None
        self.setWindowTitle(
            "Normalize audio export" if self.normalizing else "Measure file loudness"
        )
        description = self.layout().itemAt(0).widget()
        if isinstance(description, QLabel):
            description.setText(
                "Write a new verified WAV and measurement report; the source is retained."
                if self.normalizing
                else "Measure integrated LUFS, loudness range and reconstructed true peak of an audio file."
            )
        self.analyze_button.setText(
            "Export normalized WAV" if self.normalizing else "Measure loudness"
        )
        self.save_json_button.hide()
        self.save_text_button.hide()

    def start_analysis(self):
        super().start_analysis()
        if self.busy:
            self.status.setText(
                "Normalizing and verifying…" if self.normalizing else "Measuring loudness…"
            )

    def _succeeded(self, report):
        self.results.setPlainText(report.to_text())
        self.progress.setValue(1000)
        self.status.setText(
            "Verified WAV and JSON report saved."
            if self.normalizing
            else "Loudness measurement complete."
        )


def attach_loudness_delivery(window, controller):
    if hasattr(window, "loudness_dialogs"):
        return
    window.loudness_dialogs = []

    def show(normalize=False):
        options = {}
        source = ""
        if normalize:
            source, _ = QFileDialog.getOpenFileName(
                window, "Choose audio to normalize", str(window.root / "exports")
            )
            if not source:
                return
            target, ok = QInputDialog.getDouble(
                window, "Target loudness", "Integrated LUFS:", -14, -36, -5, 1
            )
            if not ok:
                return
            ceiling, ok = QInputDialog.getDouble(window, "True-peak ceiling", "dBTP:", -1, -8, 0, 1)
            if not ok:
                return
            destination, _ = QFileDialog.getSaveFileName(
                window,
                "New normalized WAV",
                str(Path(source).with_suffix(".normalized.wav")),
                "WAV (*.wav)",
            )
            if not destination:
                return
            options = dict(destination=destination, target=target, ceiling=ceiling)
        dialog = LoudnessDialog(window, **options)
        window.loudness_dialogs.append(dialog)
        dialog.finished.connect(
            lambda result: (
                window.loudness_dialogs.remove(dialog)
                if dialog in window.loudness_dialogs
                else None
            )
        )
        dialog.source.setText(source)
        dialog.show()

    menu = window.menuBar().addMenu("Loudness delivery")
    for key, title, normalize in (
        ("audio.loudness", "Measure file loudness…", False),
        ("audio.normalize", "Export loudness-normalized file…", True),
    ):
        callback = partial(show, normalize)
        controller.registry.register(
            CommandSpec(
                key, title, callback, category="Mastering", keywords=("LUFS", "true peak", "WAV")
            )
        )
        menu.addAction(title).triggered.connect(lambda checked=False, fn=callback: fn())
    controller._reindex_bindings()
