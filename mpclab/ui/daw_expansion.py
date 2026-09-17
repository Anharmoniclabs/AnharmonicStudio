"""Project audio, diagnostics and advanced-instrument controls."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ..daw_expansion_state import SAMPLE_RATES, default_daw_expansion, validate_daw_expansion


_ENGINE_LABELS = {
    "none": "Built-in / hosted plugin",
    "multisample": "Multisample regions",
    "vector": "Prism Vector A/B/C/D",
    "modal": "Physical modal resonator",
    "string": "Physical plucked string",
    "fm4": "Four-operator FM / PM",
}


class DawExpansionDialog(QDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.setWindowTitle("Project Audio & Engines")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Project-level sample rate, realtime diagnostics, streaming and the selected instrument's engine. "
            "Changing sample rate stops the current audio stream so all DSP and plugin paths can reopen on one clock."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        layout.addLayout(form)

        state = self._state()
        self.sample_rate = QComboBox()
        for rate in SAMPLE_RATES:
            self.sample_rate.addItem(f"{rate / 1000:g} kHz", rate)
        self.sample_rate.setCurrentIndex(max(0, self.sample_rate.findData(state["sample_rate"])))
        form.addRow("Project sample rate", self.sample_rate)

        self.precision = QComboBox()
        self.precision.addItem("32-bit float realtime", "float32")
        self.precision.addItem("64-bit master summing", "float64")
        self.precision.setCurrentIndex(
            max(0, self.precision.findData(state["summation_precision"]))
        )
        form.addRow("Summing", self.precision)

        self.streaming = QCheckBox("Managed decoded-audio read-ahead")
        self.streaming.setChecked(state["streaming"]["enabled"])
        form.addRow("Large sessions", self.streaming)

        self.read_ahead = QSpinBox()
        self.read_ahead.setRange(4096, 4_194_304)
        self.read_ahead.setSingleStep(4096)
        self.read_ahead.setValue(state["streaming"]["read_ahead_frames"])
        self.read_ahead.setSuffix(" frames")
        form.addRow("Read-ahead window", self.read_ahead)

        self.profiler = QCheckBox("Collect per-track / plugin performance")
        self.profiler.setChecked(
            bool(
                getattr(window.engine, "performance_profiler", None)
                and window.engine.performance_profiler.enabled
            )
        )
        form.addRow("Diagnostics", self.profiler)

        self.parallel = QSpinBox()
        self.parallel.setRange(0, 16)
        self.parallel.setSpecialValueText("Off")
        self.parallel.setValue(state["parallel_workers"])
        form.addRow("Offline graph workers", self.parallel)

        self.instrument = QComboBox()
        self.instrument.addItem("Legacy / primary instrument", None)
        for item in window.project.instruments:
            self.instrument.addItem(item.name, item.id)
        selected = window.project.selected_instrument
        self.instrument.setCurrentIndex(max(0, self.instrument.findData(selected)))
        form.addRow("Instrument", self.instrument)

        self.engine_type = QComboBox()
        for key, label in _ENGINE_LABELS.items():
            self.engine_type.addItem(label, key)
        self.instrument.currentIndexChanged.connect(self._sync_engine)
        form.addRow("Engine", self.engine_type)
        self.engine_note = QLabel()
        self.engine_note.setWordWrap(True)
        self.engine_note.setObjectName("hint")
        form.addRow(self.engine_note)
        self._sync_engine()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Apply | QDialogButtonBox.Ok
        )
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.accepted.connect(self._accept)
        layout.addWidget(buttons)

    def _state(self):
        raw = deepcopy(getattr(self.window.project, "daw_expansion", default_daw_expansion()))
        return validate_daw_expansion(raw, project=self.window.project)

    def _selected_engine(self):
        instrument_id = self.instrument.currentData()
        if instrument_id is None:
            return None
        return self._state()["instrument_engines"].get(instrument_id)

    def _sync_engine(self, *_args):
        config = self._selected_engine()
        engine_type = "none" if config is None else config["type"]
        self.engine_type.setCurrentIndex(max(0, self.engine_type.findData(engine_type)))
        self.engine_type.setEnabled(self.instrument.currentData() is not None)
        self.engine_note.setText(
            "Multisample region editing is project-data driven; Vector stores A/B/C/D and XY gesture paths; "
            "modal/string/FM engines persist with this stable instrument ID."
            if self.instrument.currentData() is not None
            else "The legacy primary instrument remains compatible with the existing global hosted-plugin slot."
        )

    @staticmethod
    def _default_engine_state(engine_type: str) -> dict:
        if engine_type == "multisample":
            return {"regions": [], "polyphony": 32}
        if engine_type == "vector":
            return {
                "sources": ["sine", "saw", "square", "triangle"],
                "x": 0.5,
                "y": 0.5,
                "gesture": [],
                "gesture_loop": False,
            }
        if engine_type == "modal":
            return {"modes": []}
        if engine_type == "string":
            return {"damping": 0.995, "brightness": 0.5, "pick_position": 0.2}
        if engine_type == "fm4":
            return {"operators": [], "algorithm": 0, "feedback": 0.0}
        return {}

    def apply(self):
        window = self.window
        project = window.project
        state = self._state()
        previous_rate = state["sample_rate"]
        state["sample_rate"] = int(self.sample_rate.currentData())
        state["summation_precision"] = str(self.precision.currentData())
        state["parallel_workers"] = self.parallel.value()
        state["streaming"]["enabled"] = self.streaming.isChecked()
        state["streaming"]["read_ahead_frames"] = self.read_ahead.value()
        instrument_id = self.instrument.currentData()
        if instrument_id is not None:
            engine_type = self.engine_type.currentData()
            if engine_type == "none":
                state["instrument_engines"].pop(instrument_id, None)
            else:
                existing = state["instrument_engines"].get(instrument_id)
                engine_state = (
                    deepcopy(existing["state"])
                    if existing is not None and existing["type"] == engine_type
                    else self._default_engine_state(engine_type)
                )
                state["instrument_engines"][instrument_id] = {
                    "type": engine_type,
                    "enabled": True,
                    "state": engine_state,
                }
        clean = validate_daw_expansion(state, project=project)
        if (
            clean == getattr(project, "daw_expansion", None)
            and bool(window.engine.performance_profiler.enabled) == self.profiler.isChecked()
        ):
            return
        window.snapshot()
        was_active = bool(
            window.engine.stream is not None and getattr(window.engine.stream, "active", False)
        )
        if was_active:
            window.engine.stop()
        project.daw_expansion = clean
        if clean["sample_rate"] != previous_rate:
            window.engine.configure_project_sample_rate(clean["sample_rate"], project)
        window.engine.configure_streaming(project)
        window.engine.rebuild_processors(project)
        window.engine.rebuild_pro_audio_graph(project)
        window.engine.sidechains.configure(project, window.engine.blocksize)
        window.engine.sync_expansion_instruments(project)
        window.engine.performance_profiler.enabled = self.profiler.isChecked()
        # Third-party plugin bridges are sample-rate specific; reload persisted
        # device/plugin state after the core engine has moved to the new clock.
        devices = getattr(window, "devices", None)
        if devices is not None and clean["sample_rate"] != previous_rate:
            devices.sync_project()
        window._set_dirty(True)
        window.status.showMessage(
            f"Project audio · {clean['sample_rate'] / 1000:g} kHz · {clean['summation_precision']}",
            5000,
        )
        if was_active:
            try:
                window.engine.start()
            except Exception as exc:
                window.status.showMessage(
                    f"Project updated; audio restart needs attention · {exc}", 8000
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

    menu = window.menuBar().addMenu("Audio engine")
    action = menu.addAction("Project Audio & Engines…")
    action.triggered.connect(show)
    window.daw_expansion_action = action
    return action
