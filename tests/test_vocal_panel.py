from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtWidgets import QCheckBox, QMessageBox

from mpclab.model import Project
from mpclab.ui.vocals import VocalPanel
from mpclab.vocal import PitchAnalysis, ProcessingCancelled


class _Engine:
    sr = 48_000
    blocksize = 256
    beat = 16.0

    def __init__(self):
        self.playing = False
        self.metronome = False
        self.commands = []

    def play(self, *_):
        self.commands.append("play")
        self.playing = True

    def stop_transport(self, *_):
        self.commands.append("stop")
        self.playing = False

    def queue_monitor(self, *_):
        pass

    def audition(self, *_):
        pass

    def stop_audition(self):
        pass


class _Library:
    def __init__(self):
        self.clips = {
            "dry": SimpleNamespace(id="dry", name="Dry vocal", kind="vocal", duration=2.0),
        }
        self._audio = np.zeros((96_000, 2), dtype=np.float32)

    def ordered(self):
        return list(self.clips.values())

    def audio(self, clip_id):
        return self._audio if clip_id in self.clips else None

    def add_audio(self, audio, name, kind):
        clip = SimpleNamespace(
            id=f"clip-{len(self.clips)}", name=name, kind=kind, duration=len(audio) / 48_000
        )
        self.clips[clip.id] = clip
        return clip


class _App:
    def __init__(self):
        self.project = Project(bpm=120.0)
        self.engine = _Engine()
        self.library = _Library()
        self.btn_metro = QCheckBox()
        self.current_clip = None
        self.playlist = SimpleNamespace(refresh=lambda: None)
        self.status = SimpleNamespace(showMessage=lambda *_: None)
        self.dirty_calls = 0
        self.snapshots = 0

    def _set_dirty(self, *_):
        self.dirty_calls += 1

    def _library_changed(self):
        pass

    def snapshot(self):
        self.snapshots += 1

    def _ensure_playlist_rows(self, count):
        while len(self.project.rows) < count:
            self.project.rows.append(SimpleNamespace(clips=[]))

    def _refresh_place_box(self):
        pass


@pytest.fixture
def panel(tmp_path):
    app = _App()
    app.root = tmp_path
    widget = VocalPanel(app)
    widget.meter_timer.stop()
    yield widget
    widget.shutdown()
    widget.deleteLater()


def _analysis():
    return PitchAnalysis(
        times=np.array([0.0], dtype=np.float32),
        detected_hz=np.array([440.0], dtype=np.float32),
        detected_midi=np.array([69.0], dtype=np.float32),
        target_midi=np.array([69.0], dtype=np.float32),
        confidence=np.array([0.9], dtype=np.float32),
    )


def _wait_for(predicate, timeout=2.0):
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for Qt worker result")


def test_key_failure_cannot_clear_an_active_tune_job(panel):
    key_cancel = threading.Event()
    tune_cancel = threading.Event()
    panel._key_job_id = 4
    panel._key_cancel = key_cancel
    panel._tune_job_id = 9
    panel._tune_cancel = tune_cancel
    panel.render_button.setText("CANCEL TUNE")

    panel._key_failed(4, "analysis problem")

    assert panel._key_cancel is None
    assert panel._tune_cancel is tune_cancel
    assert panel.render_button.text() == "CANCEL TUNE"
    assert panel.render_button.isEnabled()


def test_stale_key_and_tune_completions_are_ignored(panel):
    panel._key_job_id = 2
    panel._key_cancel = threading.Event()
    panel.key_box.setCurrentText("C")
    panel._key_finished(1, _analysis(), "F#", "minor", 0.9)
    assert panel.key_box.currentText() == "C"
    assert panel._key_cancel is not None

    panel._tune_job_id = 5
    panel._tune_cancel = threading.Event()
    before = set(panel.app.library.clips)
    panel._tune_finished(4, np.zeros((100, 2), dtype=np.float32), _analysis(), "stale")
    assert set(panel.app.library.clips) == before
    assert panel._tune_cancel is not None


def test_completion_racing_with_cancel_does_not_apply_or_save(panel):
    panel._key_job_id = 3
    panel._key_cancel = threading.Event()
    panel._key_cancel.set()
    panel.key_box.setCurrentText("C")
    panel._key_finished(3, _analysis(), "F#", "minor", 0.9)
    assert panel.key_box.currentText() == "C"
    assert panel._key_cancel is None
    assert "CANCELLED" in panel.key_progress.format()

    panel._tune_job_id = 6
    panel._tune_cancel = threading.Event()
    panel._tune_cancel.set()
    before = set(panel.app.library.clips)
    panel._tune_finished(6, np.zeros((100, 2), dtype=np.float32), _analysis(), "cancelled result")
    assert set(panel.app.library.clips) == before
    assert panel._tune_cancel is None
    assert panel.progress.format() == "TUNE CANCELLED"


def test_key_worker_can_be_cancelled_without_touching_tune(monkeypatch, panel):
    entered = threading.Event()

    def slow_analysis(*_args, cancelled=None, **_kwargs):
        entered.set()
        while cancelled is not None and not cancelled():
            time.sleep(0.001)
        raise ProcessingCancelled("key detection cancelled")

    monkeypatch.setattr("mpclab.ui.vocals.analyze_pitch", slow_analysis)
    tune_cancel = threading.Event()
    panel._tune_cancel = tune_cancel

    panel.detect_source_key()
    assert entered.wait(1.0)
    panel.detect_source_key()
    _wait_for(lambda: panel._key_cancel is None)

    assert "CANCELLED" in panel.key_progress.format()
    assert panel._tune_cancel is tune_cancel
    assert not tune_cancel.is_set()


def test_count_in_cancel_restores_transport_and_metronome(panel):
    panel.count_in.setCurrentIndex(panel.count_in.findData(1))
    panel.app.engine.playing = False
    panel.app.engine.metronome = False
    panel.app.btn_metro.setChecked(False)

    panel.toggle_recording()
    assert panel.app.engine.playing
    assert panel.app.engine.metronome
    panel.toggle_recording()

    assert panel.app.engine.commands == ["play", "stop"]
    assert not panel.app.engine.playing
    assert not panel.app.engine.metronome
    assert not panel.app.btn_metro.isChecked()


def test_capture_failure_after_count_in_restores_prior_state(monkeypatch, panel):
    monkeypatch.setattr(
        panel.recorder,
        "start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unplugged")),
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: None)
    panel.count_in.setCurrentIndex(panel.count_in.findData(1))
    panel.toggle_recording()
    token = panel._countdown_token

    panel._start_after_count(token)

    assert panel.app.engine.commands == ["play", "stop"]
    assert not panel.app.engine.metronome
    assert not panel.app.engine.playing
    assert "recording failed" in panel.record_status.text()


def test_measured_input_latency_is_applied_only_to_clip_placement(panel):
    panel.app.project.vocal_record.input_latency_ms = 125.0
    panel._place_clip("dry", beat=8.0, compensate_latency=True)

    placed = panel.app.project.rows[0].clips[-1]
    assert placed.start_beat == pytest.approx(7.75)  # 125 ms at 120 BPM
    assert placed.source_length == pytest.approx(2.0)
    assert panel.app.snapshots == 1


def test_latency_compensation_clamps_at_song_start(panel):
    panel.app.project.vocal_record.input_latency_ms = 500.0
    panel._place_clip("dry", beat=0.25, compensate_latency=True)
    assert panel.app.project.rows[0].clips[-1].start_beat == 0.0
