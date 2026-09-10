"""Project actions.

Functions receive the workstation coordinator explicitly; Qt ownership and
project state stay with that coordinator. This module owns only its named domain.
"""

from __future__ import annotations
import time
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QFileDialog,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QProgressDialog,
)
from .. import APP_NAME
from ..model import (
    Project,
    uid,
    safe_filename,
)
from ..export import ExportJob
from . import theme
from .theme import stylesheet


def _project_name_changed(window, text: str):
    window.project.name = text or "untitled"
    window._set_dirty(True)


def _apply_project(window, project: Project):
    if len(project.tracks) == len(window.engine._tbuf):
        return _apply_project_state(window, project)
    from .track_management import require_idle_capture

    require_idle_capture(window)
    previous = window.project
    undo, redo, dirty = list(window._undo), list(window._redo), window._dirty
    was_running = window.engine.stream is not None
    window.engine.stop_transport(rewind=False)
    window.engine.stop()
    prepared = False
    try:
        _apply_project_state(window, project)
        prepared = True
    except Exception:
        _apply_project_state(window, previous)
        window._undo, window._redo = undo, redo
        window._set_dirty(dirty)
        prepared = True
        raise
    finally:
        if was_running and prepared:
            try:
                window.engine.start()
            except Exception as exc:
                window.status.showMessage(
                    f"Project retained; audio output could not restart: {exc}", 10000
                )


def _apply_project_state(window, project: Project):
    automation = getattr(window, "automation_mode_controller", None)
    if automation is not None:
        automation.reset_for_project()
    window._cancel_record_count()
    window.playlist.select_clip(None)
    window.playlist.place_template = None
    window._recorded_notes.clear()
    window.sample_workflow.held.clear()
    window.sample_workflow.recorded.clear()
    window.engine.sample_panic()
    window.engine.synth_panic()
    window.project = project
    window.track_capture.armed_id = None
    window.track_inspector.row_id = None
    window.track_inspector.sync()
    window._ensure_playlist_rows()
    window.engine.project = project
    if hasattr(window, "devices"):
        window.devices.sync_project()
    window.engine.reset_fx()
    window.engine.prepare_fx(project)
    window.bpm_box.setValue(project.bpm)
    window.swing.setValue(int(project.swing))
    window.btn_cut_self.setChecked(project.self_choke)
    window.master_slider.setValue(int(project.master * 100))
    window.proj_name.setText(project.name)
    window.set_song_loop_range(project.loop_start, project.loop_end)
    window.btn_song_loop.blockSignals(True)
    window.btn_song_loop.setChecked(project.loop_enabled)
    window.btn_song_loop.blockSignals(False)
    window.engine.loop_song = project.loop_enabled
    window.apply_theme(theme.current)
    window._sync_pattern_controls()
    window._refresh_place_box()
    window.btn_only_loaded.blockSignals(True)
    window.btn_only_loaded.setChecked(window.step_grid.only_loaded)
    window.btn_only_loaded.blockSignals(False)
    window.mixer.sync()
    window.synth_panel.sync()
    window.vocal_panel.sync()
    window.automation_panel.sync()
    window.pad_inspector.set_pad(window.pads.selected)
    window.pads.update()
    window.step_grid.refresh()
    window.playlist.refresh()
    # Sample resolution belongs to the GUI/worker side. This includes
    # Playlist media and reverse buffers, so the callback never reads disk
    # or copies a whole song on the first hit.
    window.engine.preload_project_audio(project)
    window._playlist_selection_changed(window.playlist.selected_clip)


def new_project(window):
    """Start an untitled session while retaining the shared sample library."""
    if window.track_capture.busy:
        window.status.showMessage(
            "Stop and save the track take before starting a new project", 5000
        )
        return False
    if (
        window.vocal_panel.recorder.recording
        or window.vocal_panel.recorder.temporary_path is not None
        or window.vocal_panel._counting
    ):
        window.status.showMessage(
            "Finish the vocal take or cancel count-in before starting a new project", 5000
        )
        return False
    if window._dirty:
        answer = QMessageBox.question(
            window,
            "Save current project?",
            "Save your changes before starting a new project?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer != QMessageBox.Discard and (
            answer != QMessageBox.Save or not window.save_project()
        ):
            return False
    if window.session_path.exists():
        try:
            window._archive_session_recovery()
        except OSError as exc:
            QMessageBox.warning(window, "Could not preserve recovery", str(exc))
            return False
    project = Project()
    project.vocal_record.input_device = window.project.vocal_record.input_device
    project.vocal_record.input_latency_ms = window.project.vocal_record.input_latency_ms
    window.engine.stop_transport(rewind=True)
    window.engine.panic()
    window.btn_rec.setChecked(False)
    try:
        window._apply_project(project)
    except Exception as exc:
        QMessageBox.warning(window, "New project failed", str(exc))
        return False
    window._undo.clear()
    window._redo.clear()
    window.project_path = None
    window.history_path = window.session_history_path
    window.current_clip = None
    window.wave.set_clip(None, None, 0.0, [], clip_id=None)
    window.nav.set_overview(None)
    window.clip_label.setText("no sample loaded")
    window.browser.list.clearSelection()
    window.browser.list.setCurrentItem(None)
    window._rebuild_chips()
    window.proj_name.setToolTip("Project name · save to choose this project's file")
    window._set_dirty(False)
    window.status.showMessage("New untitled project", 3000)
    return True


def save_project(window):
    name = window.proj_name.text().strip() or "untitled"
    path = window.project_path or window.projects_dir / f"{safe_filename(name)}.json"
    if window.project_path is None and path.exists():
        answer = QMessageBox.question(
            window,
            "Replace existing project?",
            f"Replace {path.name}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False
    return window._save_project_to(path)


def save_project_as(window):
    initial = (
        window.project_path or window.projects_dir / f"{safe_filename(window.project.name)}.json"
    )
    path, _ = QFileDialog.getSaveFileName(
        window, "Save project as", str(initial), f"{APP_NAME} project (*.json)"
    )
    if not path:
        return False
    destination = Path(path)
    if destination.suffix.lower() != ".json":
        destination = destination.with_suffix(".json")
        if (
            destination.exists()
            and QMessageBox.question(
                window,
                "Replace existing project?",
                f"Replace {destination.name}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return False
    return window._save_project_to(destination)


def _save_project_to(window, path):
    name = window.proj_name.text().strip() or "untitled"
    window.project.name = name
    try:
        window.project.save(path)
    except (OSError, ValueError, TypeError) as exc:
        QMessageBox.warning(window, "Save failed", str(exc))
        return False
    window.project_path = Path(path)
    window.history_path = window._project_history_path(path)
    window._try_save_history()
    window.session_path.unlink(missing_ok=True)
    window.session_history_path.unlink(missing_ok=True)
    window._set_dirty(False)
    window.status.showMessage(f"saved → {path}", 4000)
    window.proj_name.setToolTip(f"Project name · saving to {path}")
    return True


def load_project(window):
    path, _ = QFileDialog.getOpenFileName(
        window, "Open project", str(window.projects_dir), f"{APP_NAME} project (*.json)"
    )
    if not path:
        return
    if window._dirty:
        answer = QMessageBox.question(
            window,
            "Save current project?",
            "Save your changes before opening another project?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Cancel or (
            answer == QMessageBox.Save and not window.save_project()
        ):
            return
    window.load_project_path(Path(path))


def load_project_path(window, path: Path, *, prepare_patch, clear_session: bool = True) -> bool:
    """Load an explicit project path for the dialog and command-line tools."""
    if window.track_capture.busy:
        window.status.showMessage("Stop and save the track take before opening a project", 5000)
        return False
    if (
        window.vocal_panel.recorder.recording
        or window.vocal_panel.recorder.temporary_path is not None
        or window.vocal_panel._counting
    ):
        window.status.showMessage("Save or discard the vocal take before opening a project", 5000)
        return False
    try:
        project = Project.load(Path(path))
        prepare_patch(project.synth)
    except Exception as exc:
        QMessageBox.warning(window, "Load failed", str(exc))
        return False
    window.engine.stop_transport(rewind=True)
    try:
        window._apply_project(project)
    except Exception as exc:
        QMessageBox.warning(window, "Load failed", str(exc))
        return False
    window.project_path = Path(path)
    window.history_path = window._project_history_path(Path(path))
    window._load_history(window.history_path)
    if clear_session:
        window.session_path.unlink(missing_ok=True)
        window.session_history_path.unlink(missing_ok=True)
    window._set_dirty(False)
    window.status.showMessage(f"loaded {Path(path).stem}", 3000)
    return True


def export_dialog(window):
    if window.export_job is not None:
        window.status.showMessage("An export is already running", 3000)
        return
    dlg = QDialog(window)
    dlg.setWindowTitle("Export WAV")
    dlg.setStyleSheet(stylesheet())
    form = QFormLayout(dlg)
    mode = QComboBox()
    mode.addItem("full arrangement (Playlist)", "song")
    mode.addItem("current pattern", "pattern")
    mode.setCurrentIndex(1 if window.engine.mode == "pattern" else 0)
    reps = QSpinBox()
    reps.setRange(1, 64)
    reps.setValue(4)
    name = QLineEdit(window.project.name)
    form.addRow("Source", mode)
    form.addRow("Pattern repeats", reps)
    form.addRow("File name", name)
    depth = QComboBox()
    depth.addItem("24-bit PCM", "PCM_24")
    depth.addItem("16-bit PCM", "PCM_16")
    depth.addItem("32-bit float", "FLOAT")
    tail = QDoubleSpinBox()
    tail.setRange(0, 30)
    tail.setValue(2.5)
    tail.setSuffix(" s")
    form.addRow("WAV format · 48 kHz stereo", depth)
    form.addRow("Effect tail", tail)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    form.addRow(buttons)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    if dlg.exec() != QDialog.Accepted:
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = window.exports_dir / f"{safe_filename(name.text() or 'mixdown')}-{stamp}-{uid()[:4]}.wav"
    window.start_export(
        out,
        mode=mode.currentData(),
        repeats=reps.value(),
        tail=tail.value(),
        subtype=depth.currentData(),
    )


def start_export(window, destination, **options):
    if window.export_job is not None:
        return False
    job = ExportJob(window.project, window.library, destination, window, **options)
    window.export_job = job
    progress = QProgressDialog("Rendering project snapshot…", "Cancel export", 0, 100, window)
    progress.setWindowTitle("Export audio")
    progress.setWindowModality(Qt.NonModal)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.canceled.connect(job.cancel)
    job.progress.connect(progress.setValue)
    job.succeeded.connect(
        lambda path, seconds: window.status.showMessage(f"Exported {seconds:.1f}s → {path}", 12000)
    )
    job.failed.connect(lambda error: QMessageBox.warning(window, "Export failed", error))
    job.cancelled.connect(lambda: window.status.showMessage("Export cancelled", 5000))

    def finished():
        progress.close()
        progress.deleteLater()
        window.export_job = None
        job.deleteLater()

    job.finished.connect(finished)
    progress.show()
    job.start()
    return True
