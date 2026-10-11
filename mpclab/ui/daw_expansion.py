"""Project sample rate dialog.

Other project-audio settings (summing precision, read-ahead streaming, offline
workers and per-instrument engines) keep their saved values but are not shown:
they are engineering controls with safe defaults, not musical choices.
"""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from ..daw_expansion_state import SAMPLE_RATES, default_daw_expansion, validate_daw_expansion


class DawExpansionDialog(QDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.setWindowTitle("Project sample rate")
        layout = QVBoxLayout(self)
        note = QLabel("Changing the sample rate briefly stops audio while the studio restarts it.")
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        layout.addLayout(form)
        self.sample_rate = QComboBox()
        for rate in SAMPLE_RATES:
            self.sample_rate.addItem(f"{rate / 1000:g} kHz", rate)
        current = self._state()["sample_rate"]
        self.sample_rate.setCurrentIndex(max(0, self.sample_rate.findData(current)))
        form.addRow("Sample rate", self.sample_rate)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._accept)
        layout.addWidget(buttons)

    def _state(self):
        raw = deepcopy(getattr(self.window.project, "daw_expansion", default_daw_expansion()))
        return validate_daw_expansion(raw, project=self.window.project)

    def apply(self):
        window = self.window
        project = window.project
        state = self._state()
        new_rate = int(self.sample_rate.currentData())
        if new_rate == state["sample_rate"]:
            return
        state["sample_rate"] = new_rate
        clean = validate_daw_expansion(state, project=project)

        window.snapshot()
        engine = window.engine
        was_playing = bool(engine.stream is not None and getattr(engine.stream, "active", False))
        if was_playing:
            engine.stop()
        project.daw_expansion = clean
        engine.configure_project_sample_rate(new_rate, project)
        engine.configure_streaming(project)
        engine.rebuild_processors(project)
        engine.rebuild_pro_audio_graph(project)
        engine.sidechains.configure(project, engine.blocksize)
        engine.sync_expansion_instruments(project)
        # Plugin bridges run at a fixed rate, so reload them on the new clock.
        devices = getattr(window, "devices", None)
        if devices is not None:
            devices.sync_project()
        window._set_dirty(True)
        window.status.showMessage(f"Project sample rate · {new_rate / 1000:g} kHz", 5000)
        if was_playing:
            try:
                engine.start()
            except Exception as exc:
                window.status.showMessage(
                    f"Sample rate changed; audio did not restart · {exc}", 8000
                )

    def _accept(self):
        self.apply()
        self.accept()


def attach_daw_expansion(window, _controller=None):
    existing = getattr(window, "daw_expansion_action", None)
    if existing is not None:
        return existing

    def show():
        dialog = DawExpansionDialog(window)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        window._daw_expansion_dialog = dialog
        dialog.show()
        dialog.raise_()

    # The menu organizer moves this menu under Sound.
    menu = window.menuBar().addMenu("Audio engine")
    action = menu.addAction("Sample rate…")
    action.triggered.connect(show)
    window.daw_expansion_action = action
    return action
