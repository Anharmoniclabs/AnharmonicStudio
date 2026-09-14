"""Explicit MIDI file commands with preview, playback disclosure and atomic undo."""

from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from ..midi_smf import read_midi
from ..midi_file_state import (
    EXPORT_NOTICE,
    PLAYBACK_NOTICE,
    export_imported_midi,
    export_pattern_midi,
    imported_project,
    midi_sources,
    midi_summary,
    save_midi,
)
from ..model import safe_filename
from ..workflow_commands import CommandSpec
from .track_management import require_idle_capture


class MidiImportDialog(QDialog):
    def __init__(self, parent, name, summary):
        super().__init__(parent)
        self.setWindowTitle("Import Standard MIDI file")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        heading = QLabel(
            f"{name}\nSMF {summary['format']} · {summary['tracks']} tracks · "
            f"{summary['notes']} notes · {summary['ticks_per_quarter']} ticks/quarter\n"
            f"{summary['tempo_events']} tempo events · {summary['meter_events']} meter events · "
            f"{summary['controller_events']} controller events"
        )
        heading.setTextFormat(Qt.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        notice = QLabel(PLAYBACK_NOTICE)
        notice.setTextFormat(Qt.PlainText)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.use_tempo = QCheckBox("Use the initial file tempo for this project (40–240 BPM)")
        self.use_tempo.setChecked(False)
        layout.addWidget(self.use_tempo)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Import patterns and preserve MIDI data")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MidiFileController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def import_path(self, path, *, use_initial_tempo=False, raw=None):
        window = self.window
        require_idle_capture(window)
        if window.engine.playing:
            raise RuntimeError("Stop playback before importing MIDI patterns")
        raw = read_midi(path)[0] if raw is None else raw
        candidate, source = imported_project(
            window.project,
            raw,
            Path(path).name,
            use_initial_tempo=use_initial_tempo,
        )
        previous = window.project
        undo, redo, dirty = list(window._undo), list(window._redo), window._dirty
        history = window._history_state()
        try:
            window._apply_project(candidate)
        except Exception:
            if window.project is not previous:
                window._apply_project(previous)
            window._undo, window._redo = undo, redo
            window._set_dirty(dirty)
            raise
        window._undo = [*undo, history][-40:]
        window._redo = []
        window._try_save_history()
        window._set_dirty(True)
        window.status.showMessage(
            "Imported MIDI patterns · tempo/maps/controllers preserved, not fully replayed",
            12000,
        )
        return source["id"]

    def import_dialog(self):
        try:
            require_idle_capture(self.window)
            if self.window.engine.playing:
                raise RuntimeError("Stop playback before importing MIDI patterns")
            path, _ = QFileDialog.getOpenFileName(
                self.window,
                "Import Standard MIDI file",
                str(self.window.root),
                "Standard MIDI files (*.mid *.midi)",
            )
            if not path:
                return False
            raw, document = read_midi(Path(path))
            dialog = MidiImportDialog(self.window, Path(path).name, midi_summary(document))
            if dialog.exec() != QDialog.Accepted:
                return False
            self.import_path(path, raw=raw, use_initial_tempo=dialog.use_tempo.isChecked())
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self.window, "MIDI import failed", str(exc))
            return False

    def _save_dialog(self, data, name):
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Export Standard MIDI file",
            str(self.window.exports_dir / f"{safe_filename(name)}.mid"),
            "Standard MIDI files (*.mid)",
        )
        if not path:
            return False
        destination = Path(path)
        if destination.suffix.lower() not in (".mid", ".midi"):
            destination = destination.with_suffix(".mid")
        overwrite = destination.exists()
        if (
            overwrite
            and QMessageBox.question(
                self.window,
                "Replace MIDI file?",
                f"Replace {destination.name}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return False
        save_midi(destination, data, overwrite=overwrite)
        self.window.status.showMessage(f"Exported MIDI → {destination}", 6000)
        return True

    def export_imported_dialog(self):
        try:
            sources = midi_sources(self.window.project)
            if not sources:
                raise ValueError(
                    "No imported MIDI sources; use Export current note pattern instead"
                )
            labels = [f"{index + 1} · {source['name']}" for index, source in enumerate(sources)]
            choice, accepted = QInputDialog.getItem(
                self.window,
                "Export imported MIDI with current note edits",
                EXPORT_NOTICE,
                labels,
                0,
                False,
            )
            if not accepted:
                return False
            source = sources[labels.index(choice)]
            data = export_imported_midi(self.window.project, source["id"])
            return self._save_dialog(data, Path(source["name"]).stem)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self.window, "MIDI export failed", str(exc))
            return False

    def export_pattern_dialog(self):
        try:
            choice, accepted = QInputDialog.getItem(
                self.window,
                "Export current note pattern",
                "One note pattern only, not Playlist/audio/sampler steps. Constant project tempo, "
                "4/4 meter and stored MIDI channels; no instrument patches/controllers are generated.",
                ["Type 1 · conductor and note track", "Type 0 · single track"],
                0,
                False,
            )
            if not accepted:
                return False
            data = export_pattern_midi(
                self.window.project, format=1 if choice.startswith("Type 1") else 0
            )
            return self._save_dialog(data, self.window.project.pattern().name)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self.window, "MIDI export failed", str(exc))
            return False


def attach_midi_files(window, controller):
    existing = getattr(window, "midi_file_controller", None)
    if existing is not None:
        return existing
    midi = MidiFileController(window)
    menu = window.menuBar().addMenu("MIDI files")
    for identifier, title, callback in (
        ("midi.file_import", "Import Standard MIDI file…", midi.import_dialog),
        ("midi.file_export", "Export imported MIDI with note edits…", midi.export_imported_dialog),
        ("midi.pattern_export", "Export current note pattern…", midi.export_pattern_dialog),
    ):
        controller.registry.register(
            CommandSpec(
                identifier,
                title,
                callback,
                category="MIDI files",
                keywords=("SMF", "notes", "tempo", "channels", "interchange"),
            )
        )
        action = menu.addAction(title)
        action.triggered.connect(
            lambda _checked=False, command=identifier: controller.registry.execute(command)
        )
    controller._reindex_bindings()
    window.midi_file_controller = midi
    return midi
