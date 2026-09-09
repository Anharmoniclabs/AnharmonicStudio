"""First-run audio routing and round-trip calibration dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
)

from ..audio_setup import (
    LatencyCalibration,
    profile_description,
    recommended_buffer,
    run_loopback_calibration,
)


class AudioSetupDialog(QDialog):
    """Collect a durable device/profile choice without owning the engine."""

    def __init__(
        self,
        outputs: list[dict],
        inputs: list[dict],
        parent=None,
        calibration_runner=run_loopback_calibration,
    ):
        super().__init__(parent)
        self.setWindowTitle("Audio setup")
        self.setModal(False)
        self.outputs = list(outputs)
        self.inputs = list(inputs)
        self.calibration_runner = calibration_runner
        self.calibration: LatencyCalibration | None = None

        form = QFormLayout(self)
        intro = QLabel(
            "Choose the devices you actually use. Start safe; lower the buffer "
            "only after the DSP meter stays clean."
        )
        intro.setWordWrap(True)
        form.addRow(intro)

        self.output_box = QComboBox()
        self.output_box.addItem("System default output", "")
        for item in self.outputs:
            self.output_box.addItem(item["label"], item["key"])
        form.addRow("Output", self.output_box)

        self.input_box = QComboBox()
        self.input_box.addItem("System default input", "")
        for item in self.inputs:
            self.input_box.addItem(item["label"], item["key"])
        form.addRow("Loopback input", self.input_box)

        self.workflow_box = QComboBox()
        self.workflow_box.addItem("Building / heavy session", "build")
        self.workflow_box.addItem("Normal production", "production")
        self.workflow_box.addItem("Light live tracking (experimental)", "live")
        self.workflow_box.currentIndexChanged.connect(self._refresh_recommendation)
        form.addRow("Workflow", self.workflow_box)

        self.recommendation = QLabel()
        self.recommendation.setWordWrap(True)
        form.addRow("Recommended", self.recommendation)

        self.test_button = QPushButton("RUN CABLED LOOPBACK TEST")
        self.test_button.setToolTip(
            "Connect an output to an input with a cable. Keep speakers low."
        )
        self.test_button.clicked.connect(self._run_loopback)
        form.addRow("Latency", self.test_button)
        self.calibration_label = QLabel(
            "Optional · connect the selected output to the selected input."
        )
        self.calibration_label.setWordWrap(True)
        form.addRow("Result", self.calibration_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("USE THIS SETUP")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._refresh_recommendation()

    @property
    def recommended_frames(self) -> int:
        return recommended_buffer(str(self.workflow_box.currentData() or "build"))

    @property
    def output_key(self) -> str:
        return str(self.output_box.currentData() or "")

    @property
    def input_key(self) -> str:
        return str(self.input_box.currentData() or "")

    def select_saved(
        self, output_key: str = "", input_key: str = "", workflow: str = "build"
    ) -> None:
        for box, value in (
            (self.output_box, output_key),
            (self.input_box, input_key),
            (self.workflow_box, workflow),
        ):
            index = box.findData(value)
            if index >= 0:
                box.setCurrentIndex(index)

    def _refresh_recommendation(self, *_args) -> None:
        self.recommendation.setText(profile_description(self.recommended_frames))

    def _selected_device_index(self, items: list[dict], key: str):
        selected = next((item for item in items if item.get("key") == key), None)
        return selected.get("index") if selected is not None else None

    def _run_loopback(self) -> None:
        self.test_button.setEnabled(False)
        self.calibration_label.setText("Testing… keep the loopback cable connected.")
        try:
            self.calibration = self.calibration_runner(
                self._selected_device_index(self.inputs, self.input_key),
                self._selected_device_index(self.outputs, self.output_key),
            )
        except Exception as exc:
            self.calibration = None
            self.calibration_label.setText(f"Test failed · {exc}")
            QMessageBox.warning(self, "Loopback test failed", str(exc))
        else:
            result = self.calibration
            self.calibration_label.setText(
                f"{result.milliseconds:.2f} ms round trip · {result.confidence:.0%} confidence"
            )
        finally:
            self.test_button.setEnabled(True)
