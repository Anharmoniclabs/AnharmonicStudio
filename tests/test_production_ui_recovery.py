"""Failure recovery and mixer edits must preserve the working session UI."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QLineEdit

from mpclab.engine import Engine
from mpclab.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda _engine, device=None: None)
    app = MainWindow(tmp_path)
    app._ui_timer.stop()
    app._autosave_timer.stop()
    try:
        yield app
    finally:
        app.track_capture.unsaved = None
        app.engine.stream = None
        app.close()


def fail_output(window):
    calls = []
    window.engine.stream = SimpleNamespace(
        render_error="synthetic render failure",
        stop=lambda: calls.append("stop"),
        close=lambda: calls.append("close"),
    )
    return calls


def start_note_take(window):
    row = window.project.rows[0]
    row.record_source = "notes"
    window.track_capture.arm(row)
    assert window.track_capture.prepare()
    window.track_capture.start()
    window.engine.playing = True
    window.track_capture.note_on(60, 0.7)
    window.engine.beat = 1.5
    window.btn_rec.blockSignals(True)
    window.btn_rec.setChecked(True)
    window.btn_rec.blockSignals(False)
    return row


def test_render_failure_finishes_note_take_and_clears_transport_once(window):
    row = start_note_take(window)
    calls = fail_output(window)

    window._tick()

    assert not window.track_capture.active
    assert not window.engine.playing
    assert not window.engine.recording
    assert not window.btn_rec.isChecked()
    assert not window.btn_play.isChecked()
    assert len(row.clips) == 1
    take = window.project.pattern(row.clips[0].ref)
    assert len(take.notes) == 1
    assert take.notes[0].duration == pytest.approx(1.5)
    assert calls == ["stop", "close"]
    assert window.cpu_label.text() == "audio render error"
    assert "Check the take" in window.status.currentMessage()

    window.status.showMessage("User is reviewing the saved take")
    window._tick()
    assert len(row.clips) == 1
    assert calls == ["stop", "close"]
    assert window.status.currentMessage() == "User is reviewing the saved take"


def test_render_failure_cancels_count_in_without_starting_capture(window):
    row = window.project.rows[0]
    row.record_source = "notes"
    window.track_capture.arm(row)
    assert window.track_capture.prepare()
    window._record_count_deadline = 123456.0
    window.btn_rec.blockSignals(True)
    window.btn_rec.setChecked(True)
    window.btn_rec.blockSignals(False)
    fail_output(window)

    window._tick()

    assert not window.track_capture.pending
    assert not window.track_capture.active
    assert window._record_count_deadline is None
    assert not window._record_count_timer.isActive()
    assert not window.btn_rec.isChecked()
    assert not row.clips


def test_render_failure_keeps_an_unsaved_take_available_for_retry(window, monkeypatch):
    start_note_take(window)
    monkeypatch.setattr(window.track_capture, "save_take", lambda: None)
    fail_output(window)

    window._tick()

    assert not window.track_capture.active
    assert window.track_capture.unsaved is not None
    assert len(window.track_capture.unsaved[1]) == 1
    assert not window.engine.playing
    assert not window.btn_rec.isChecked()


def test_mixer_fader_preserves_pad_editor_and_pending_text(window):
    pad = window.project.pads[0]
    pad.sample_id = "synthetic-sample"
    window.pad_inspector.set_pad(0)
    editor = window.pad_inspector.findChild(QLineEdit)
    editor.setText("Unfinished pad name")
    parent = editor.parent()

    for gain in (90, 80, 75):
        window.mixer.strips[0].fader.setValue(gain)

    assert window.pad_inspector.findChild(QLineEdit) is editor
    assert editor.parent() is parent
    assert editor.text() == "Unfinished pad name"
    assert window.project.tracks[0].gain == pytest.approx(0.75)
    assert window._dirty


def test_mixer_rename_and_fx_refresh_routing_without_rebuilding(window):
    pad = window.project.pads[0]
    pad.sample_id = "synthetic-sample"
    pad.track = 1
    window.pad_inspector.set_pad(0)
    editor = window.pad_inspector.findChild(QLineEdit)
    out = window.pad_inspector._output_combo
    changes = []
    out.currentIndexChanged.connect(changes.append)
    window.project.tracks[1].name = "Lead"
    window.project.tracks[1].fx.drive = 0.5

    window._mixer_changed()

    assert "Lead" in out.itemText(1)
    assert "fx" in out.itemText(1)
    assert out.currentIndex() == 1
    assert pad.track == 1
    assert not changes
    assert window.pad_inspector.findChild(QLineEdit) is editor
