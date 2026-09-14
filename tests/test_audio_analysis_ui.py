from __future__ import annotations

import gc
import json
import threading
import time

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow
from shiboken6 import isValid

from mpclab.audio_analysis import AnalysisCancelled, analyze_audio_file
from mpclab.ui.audio_analysis import AudioAnalysisDialog, AudioAnalysisJob, attach_audio_analysis
from mpclab.workflow_commands import CommandRegistry


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        QCoreApplication.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for the offscreen analysis worker.")
        time.sleep(0.005)
    QCoreApplication.processEvents()


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "render.wav"
    signal = 0.25 * np.sin(2 * np.pi * np.arange(8000) / 8)
    sf.write(path, np.column_stack((signal, -signal)), 8000, subtype="FLOAT")
    return path


def test_dialog_measures_real_file_off_main_thread_and_saves_report(
    audio_file, tmp_path, monkeypatch
):
    main_thread = threading.get_ident()
    analysis_threads = []

    def analyze(*args, **kwargs):
        analysis_threads.append(threading.get_ident())
        return analyze_audio_file(*args, **kwargs)

    dialog = AudioAnalysisDialog(analysis_function=analyze)
    dialog.source.setText(str(audio_file))
    assert not dialog.save_json_button.isEnabled()
    assert not dialog.cancel_button.isEnabled()
    dialog.start_analysis()
    assert dialog.busy
    assert not dialog.analyze_button.isEnabled()
    wait_for(lambda: not dialog.busy)
    assert analysis_threads and analysis_threads[0] != main_thread
    assert dialog.report.frames == 8000
    assert dialog.report.stereo.correlation == pytest.approx(-1)
    assert dialog.save_json_button.isEnabled()
    assert dialog.save_text_button.isEnabled()
    assert dialog.progress.value() == 1000
    assert "LUFS and true peak were not measured" in dialog.status.text()
    assert "AC stereo correlation: -1.000000" in dialog.results.toPlainText()
    output = tmp_path / "report.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(output), "Report (*.json)"))
    dialog.save_json_button.click()
    assert json.loads(output.read_text())["frames"] == 8000
    assert "Report saved" in dialog.status.text()


def test_empty_path_and_file_failure_remain_recoverable(audio_file):
    dialog = AudioAnalysisDialog()
    dialog.start_analysis()
    assert not dialog.busy
    assert "Choose an audio file" in dialog.status.text()
    dialog.source.setText(str(audio_file.parent / "missing.wav"))
    dialog.start_analysis()
    wait_for(lambda: not dialog.busy)
    assert "Analysis failed" in dialog.status.text()
    assert dialog.report is None
    assert not dialog.save_json_button.isEnabled()
    assert dialog.analyze_button.isEnabled()
    dialog.source.setText(str(audio_file))
    dialog.start_analysis()
    wait_for(lambda: not dialog.busy)
    assert dialog.report is not None


@pytest.mark.parametrize("close_method", ["cancel", "reject", "close"])
def test_cancel_and_close_wait_for_worker_without_publishing(audio_file, close_method):
    started = threading.Event()
    cancelled = threading.Event()

    def delayed_analysis(path, *, cancel, progress):
        started.set()
        progress(2**32, 2**33)  # Progress cannot overflow a Qt int signal.
        assert cancel.wait(3), "The dialog did not request cancellation."
        cancelled.set()
        raise AnalysisCancelled()

    dialog = AudioAnalysisDialog(analysis_function=delayed_analysis)
    dialog.show()  # Test-only QApplication is always offscreen.
    dialog.source.setText(str(audio_file))
    dialog.start_analysis()
    wait_for(started.is_set)
    if close_method == "cancel":
        dialog.cancel_button.click()
    elif close_method == "reject":
        dialog.reject()
    else:
        dialog.close()
    wait_for(lambda: not dialog.busy)
    assert cancelled.is_set()
    assert dialog.report is None
    assert not dialog.save_json_button.isEnabled()
    assert "Analysis cancelled" in dialog.status.text()
    if close_method == "cancel":
        assert dialog.isVisible()
        assert dialog.analyze_button.isEnabled()
    else:
        assert not dialog.isVisible()


def test_late_cancel_discards_already_queued_result(audio_file):
    dialog = AudioAnalysisDialog()
    dialog.source.setText(str(audio_file))
    dialog.start_analysis()
    job = dialog._job
    # Do not dispatch the queued success signal before requesting cancellation.
    job._thread.join(timeout=3)
    assert not job._thread.is_alive()
    dialog.cancel_analysis()
    wait_for(lambda: not dialog.busy)
    assert dialog.report is None
    assert "cancelled" in dialog.status.text()


def test_deleting_parent_cancels_job_and_worker_can_exit_safely(audio_file):
    parent = QMainWindow()
    started = threading.Event()
    stopped = threading.Event()

    def wait_for_cancel(path, *, cancel, progress):
        started.set()
        assert cancel.wait(3), "Destruction must cancel the read."
        stopped.set()
        raise AnalysisCancelled()

    dialog = AudioAnalysisDialog(parent, analysis_function=wait_for_cancel)
    dialog.source.setText(str(audio_file))
    dialog.start_analysis()
    worker = dialog._job._thread
    wait_for(started.is_set)
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    wait_for(stopped.is_set)
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert not isValid(parent)


def test_menu_command_is_wired_idempotent_and_survives_collection(tmp_path):
    class Commands:
        def __init__(self):
            self.registry = CommandRegistry(tmp_path / "commands.json")
            self.reindexed = 0
            self.executed = []

        def _reindex_bindings(self):
            self.reindexed += 1

        def _execute(self, command):
            self.executed.append(command)
            return self.registry.execute(command)

    window = QMainWindow()
    menu = window.menuBar().addMenu("&File")
    menu.addAction("Existing action")
    commands = Commands()
    controller = attach_audio_analysis(window, commands)
    assert attach_audio_analysis(window, commands) is controller
    del menu
    gc.collect()
    assert isValid(controller.file_menu)
    assert isValid(controller.file_menu_action)
    assert [item.text() for item in controller.file_menu.actions()] == [
        "Existing action",
        "Analyze rendered audio…",
    ]
    assert commands.reindexed == 1
    assert commands.registry.get("audio.analyze_file").category == "File"
    controller.action.trigger()
    assert commands.executed == ["audio.analyze_file"]
    assert controller.dialog.isVisible()
    first_dialog = controller.dialog
    controller.dialog.reject()
    controller.action.trigger()
    assert controller.dialog is first_dialog
    assert "true peak" in " ".join(
        widget.text() for widget in controller.dialog.findChildren(type(controller.dialog.status))
    )


def test_saving_over_source_is_rejected_in_dialog(audio_file, monkeypatch):
    before = audio_file.read_bytes()
    dialog = AudioAnalysisDialog()
    dialog.report = analyze_audio_file(audio_file)
    dialog._set_busy(False)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_: (str(audio_file), "Report (*.json)")
    )
    dialog.choose_report_destination("json")
    assert audio_file.read_bytes() == before
    assert "cannot replace its source audio" in dialog.status.text()


def test_job_cannot_be_started_twice_and_thread_start_failure_recovers(audio_file, monkeypatch):
    dialog = AudioAnalysisDialog()
    job = AudioAnalysisJob(audio_file, dialog)
    job.start()
    with pytest.raises(RuntimeError, match="only be started once"):
        job.start()
    job._thread.join(timeout=3)
    assert not job._thread.is_alive()

    def fail_start(self):
        raise RuntimeError("simulated worker start failure")

    monkeypatch.setattr(AudioAnalysisJob, "start", fail_start)
    dialog.source.setText(str(audio_file))
    dialog.start_analysis()
    assert not dialog.busy
    assert "worker start failure" in dialog.status.text()
    assert dialog.analyze_button.isEnabled()
    assert dialog.report is None


def test_analysis_ui_test_environment_is_offscreen():
    assert QApplication.instance().platformName() == "offscreen"
