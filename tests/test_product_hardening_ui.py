"""GUI/model integration for recovery, history, setup, and vocal takes."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mpclab.audio_setup import LatencyCalibration
from mpclab.engine import Engine
from mpclab.ui import main_window, vocals
from mpclab.ui.audio_setup import AudioSetupDialog
from mpclab.ui.main_window import MainWindow


class _MemorySettings:
    IniFormat = object()
    UserScope = object()
    values: dict[str, object] = {}

    def __init__(self, *_args, **_kwargs):
        pass

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


@pytest.fixture
def window(tmp_path, monkeypatch):
    _MemorySettings.values = {"audio/setup_complete": True}
    monkeypatch.setattr(main_window, "QSettings", _MemorySettings)
    monkeypatch.setattr(Engine, "start", lambda _engine, device=None: None)
    instance = MainWindow(tmp_path, restore_session=False)
    try:
        yield instance
    finally:
        instance.engine.stream = None
        instance.close()


def test_audio_setup_persists_devices_workflow_and_latency(window):
    output = {"index": 7, "key": "output-key", "label": "USB out"}
    input_ = {"index": 9, "key": "input-key", "label": "USB in"}
    dialog = AudioSetupDialog([output], [input_], window)
    dialog.select_saved("output-key", "input-key", "production")
    dialog.calibration = LatencyCalibration(480, 48_000, 0.92)
    switched = []
    window._select_audio_output = switched.append

    window._apply_audio_setup(dialog)

    assert switched == ["output-key"]
    assert window.engine.blocksize == 256
    assert _MemorySettings.values["audio/setup_complete"] is True
    assert _MemorySettings.values["audio/input_device"] == "input-key"
    assert _MemorySettings.values["audio/roundtrip_latency_ms"] == 10.0
    assert window.project.vocal_record.input_device == "input-key"
    assert window.project.vocal_record.input_latency_ms == 10.0


def test_file_new_project_clears_session_but_retains_library_and_saved_file(window):
    clip = window.library.add_audio(np.zeros((480, 2), dtype=np.float32), "Keep this sample")
    window.project.pads[0].sample_id = clip.id
    window.proj_name.setText("Old song")
    assert window.save_project()
    saved = window.project_path
    saved_bytes = saved.read_bytes()
    window._undo.append("old undo")
    window._redo.append("old redo")
    window.current_clip = clip.id
    window.project.vocal_record.input_latency_ms = 12.5
    menu_action = window.menuBar().actions()[0]
    file_menu = menu_action.menu()
    action = file_menu.actions()[0]
    assert action.text() == "New project\tCtrl+N"
    assert any(shortcut.key().toString() == "Ctrl+N" for shortcut in window._shortcuts)
    action.trigger()
    assert window.project.name == "untitled"
    assert all(pad.empty for pad in window.project.pads)
    assert window.project_path is None
    assert window.history_path == window.session_history_path
    assert not window._undo and not window._redo
    assert window.current_clip is None
    assert window.project.vocal_record.input_latency_ms == 12.5
    assert clip.id in window.library.clips
    assert saved.read_bytes() == saved_bytes


@pytest.mark.parametrize(
    "choice,save_result,created",
    [
        ("Cancel", True, False),
        ("Save", False, False),
        ("Save", True, True),
        ("Discard", True, True),
    ],
)
def test_new_project_respects_unsaved_choice_and_save_failure(
    window, monkeypatch, choice, save_result, created
):
    from PySide6.QtWidgets import QMessageBox

    original = window.project
    window._set_dirty(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: getattr(QMessageBox, choice))
    saved = []
    monkeypatch.setattr(window, "save_project", lambda: saved.append(True) or save_result)
    assert window.new_project() is created
    assert (window.project is not original) is created
    assert bool(saved) is (choice == "Save")
    if not created:
        assert window._dirty


def test_new_project_archives_existing_recovery(window):
    window.project.name = "Recoverable song"
    window.project.save(window.session_path)
    original = window.session_path.read_bytes()
    assert window.new_project()
    archived = list(window.projects_dir.glob("recovery-skipped-*.json"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == original
    assert not window.session_path.exists()


def test_place_take_is_undoable_redoable_and_latency_compensated(window):
    dry = window.library.add_audio(np.zeros((48_000, 2), dtype=np.float32), "lead", kind="vocal")
    window.project.bpm = 120.0
    window.project.vocal_record.input_latency_ms = 25.0

    window.vocal_panel._place_clip(dry.id, 8.0, compensate_latency=True)

    placed = window.project.rows[0].clips[-1]
    assert placed.start_beat == pytest.approx(7.95)
    assert window.history_path.exists()
    window.undo()
    assert not window.project.rows[0].clips
    window.redo()
    assert window.project.rows[0].clips[-1].ref == dry.id


def test_vocal_take_management_links_ab_pairs_and_guards_used_delete(window, monkeypatch):
    audio = np.zeros((4_800, 2), dtype=np.float32)
    dry = window.library.add_audio(audio, "lead", kind="vocal")
    tuned = window.library.add_audio(audio, "lead tuned", kind="vocal-tuned", parent=dry.id)
    panel = window.vocal_panel
    panel.refresh_takes(select=dry.id)
    auditioned = []
    window.engine.audition = lambda clip_id, *_args: auditioned.append(clip_id)

    panel.audition_related_tuned()
    panel.take_box.setCurrentIndex(panel.take_box.findData(tuned.id))
    panel.audition_related_dry()
    assert auditioned == [tuned.id, dry.id]

    monkeypatch.setattr(
        vocals.QInputDialog, "getText", lambda *_args, **_kwargs: ("Lead Final", True)
    )
    panel.rename_selected_take()
    assert window.library.clips[tuned.id].name == "Lead Final"

    panel.duplicate_selected_take()
    duplicate = window.library.clips[panel.take_box.currentData()]
    assert duplicate.kind == "vocal-tuned"
    assert duplicate.parent == dry.id

    panel._place_clip(duplicate.id, 0.0)
    warnings = []
    monkeypatch.setattr(
        vocals.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    panel.delete_selected_take()
    assert duplicate.id in window.library.clips
    assert "Playlist" in warnings[-1][1]


def test_crash_recovery_restores_persistent_undo_and_redo(tmp_path, monkeypatch):
    _MemorySettings.values = {"audio/setup_complete": True}
    monkeypatch.setattr(main_window, "QSettings", _MemorySettings)
    monkeypatch.setattr(Engine, "start", lambda _engine, device=None: None)
    first = MainWindow(tmp_path, restore_session=False)
    first.snapshot()
    first.project.name = "Recovered beat"
    first.project.bpm = 133.0
    first._autosave_session()
    first.engine.stream = None
    first.close()

    monkeypatch.setattr(MainWindow, "_choose_session_recovery", lambda _window, _project: "restore")
    recovered = MainWindow(tmp_path)
    try:
        assert recovered.project.name == "Recovered beat"
        assert recovered.project.bpm == 133.0
        assert len(recovered._undo) == 1
        recovered.undo()
        assert recovered.project.name == "untitled"
        recovered.redo()
        assert recovered.project.name == "Recovered beat"
    finally:
        recovered.engine.stream = None
        recovered.close()


def test_named_project_reopens_its_undo_history(tmp_path, monkeypatch):
    _MemorySettings.values = {"audio/setup_complete": True}
    monkeypatch.setattr(main_window, "QSettings", _MemorySettings)
    monkeypatch.setattr(Engine, "start", lambda _engine, device=None: None)
    first = MainWindow(tmp_path, restore_session=False)
    first.proj_name.setText("History song")
    first.snapshot()
    first.project.bpm = 144.0
    first.save_project()
    project_path = first.projects_dir / "History song.json"
    first.engine.stream = None
    first.close()

    reopened = MainWindow(tmp_path, restore_session=False)
    try:
        assert reopened.load_project_path(project_path)
        assert reopened.project.bpm == 144.0
        reopened.undo()
        assert reopened.project.bpm == 90.0
    finally:
        reopened.engine.stream = None
        reopened.close()


def test_save_preserves_opened_path_after_project_rename(window, tmp_path):
    from mpclab.model import Project

    path = tmp_path / "other-folder" / "original.json"
    Project(name="original").save(path)
    assert window.load_project_path(path)
    window.proj_name.setText("Renamed session")
    window.project.bpm = 137
    assert window.save_project()
    assert Project.load(path).bpm == 137
    assert Project.load(path).name == "Renamed session"
    assert not (window.projects_dir / "Renamed session.json").exists()


def test_save_as_changes_destination_and_cancellation_keeps_it(window, monkeypatch, tmp_path):
    from mpclab.model import Project

    destination = tmp_path / "new-song.json"
    monkeypatch.setattr(
        main_window.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(destination), "")
    )
    assert window.save_project_as()
    assert window.project_path == destination
    window.project.bpm = 151
    assert window.save_project()
    assert Project.load(destination).bpm == 151
    monkeypatch.setattr(
        main_window.QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", "")
    )
    assert not window.save_project_as()
    assert window.project_path == destination


def test_failed_save_as_keeps_previous_path_and_dirty_state(window, monkeypatch, tmp_path):
    from mpclab.model import Project

    assert window.save_project()
    previous = window.project_path
    window._set_dirty(True)
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("disk full")

        patch.setattr(Project, "save", fail)
        assert not window._save_project_to(tmp_path / "failed.json")
    assert window.project_path == previous
    assert window._dirty
    assert warnings == ["disk full"]


def test_failed_recovery_prevents_closing_daw_session(window, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from mpclab.model import Project

    window._set_dirty(True)
    warnings = []
    event = QCloseEvent()
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("disk full")

        patch.setattr(Project, "save", fail)
        window.closeEvent(event)
    assert not event.isAccepted()
    assert "disk full" in warnings[0]


def test_start_clean_archives_recovery_instead_of_deleting_it(window):
    window.project.name = "Keep me"
    window._set_dirty(True)
    window._autosave_session()

    window._restore_session(choice="archive")

    assert not window.session_path.exists()
    archived = list(window.projects_dir.glob("recovery-skipped-*.json"))
    assert len(archived) == 1
    assert main_window.Project.load(archived[0]).name == "Keep me"


def test_tick_recommends_next_profile_after_xrun(window):
    window.engine.stream = SimpleNamespace(latency=0.01)
    window.engine.underruns = 1
    window.engine.timing_stats = lambda: {
        "p50": 1.0,
        "p99": 2.0,
        "max": 2.5,
        "period": 10.67,
        "headroom": 0.8,
        "xruns": 1,
        "blocks": 30,
    }

    window._tick()

    assert "TRY 1024" in window.cpu_label.text()
    assert "more headroom" in window.status.currentMessage()


def test_deferred_recovery_survives_next_autosave(window):
    from mpclab.model import Project

    Project(name="Previous idea").save(window.session_path)
    window._restore_session(choice="cancel")
    window.project.name = "New idea"
    window._set_dirty(True)
    window._autosave_session()
    archived = list(window.projects_dir.glob("recovery-skipped-*.json"))
    assert any(Project.load(path).name == "Previous idea" for path in archived)
    assert Project.load(window.session_path).name == "New idea"


def test_recovery_versions_are_bounded_and_preserve_prior_save(window):
    from mpclab.model import Project

    window._set_dirty(True)
    for index in range(13):
        window.project.name = f"Idea {index}"
        window._autosave_session()
    versions = [
        p
        for p in (window.projects_dir / "recovery-versions").glob("*.json")
        if not p.name.endswith(".history.json")
    ]
    assert len(versions) == 10
    assert "Idea 11" in {Project.load(path).name for path in versions}
    assert Project.load(window.session_path).name == "Idea 12"


def test_sidebar_preferences_survive_focus_mode(window):
    window.pad_side.show()
    window.toggle_pads()
    assert window.pad_side.isHidden()
    window.set_playlist_focus(True)
    window.set_playlist_focus(False)
    assert window.pad_side.isHidden()
    window._save_panel_layout()
    assert window.settings.value("ui/pads_visible") is False
    window.toggle_pads()
    assert not window.pad_side.isHidden()


def test_session_lock_rejects_second_owner_and_releases(tmp_path):
    from PySide6.QtCore import QLockFile

    path = str(tmp_path / "session.lock")
    first, second = QLockFile(path), QLockFile(path)
    first.setStaleLockTime(0)
    second.setStaleLockTime(0)
    assert first.tryLock(0)
    assert not second.tryLock(0)
    first.unlock()
    assert second.tryLock(0)
    second.unlock()


def test_record_counts_three_beats_before_starting(window, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(main_window.time, "monotonic", lambda: now[0])
    window.project.bpm = 120
    starts = []
    monkeypatch.setattr(window.engine, "play", lambda: starts.append(True))
    window.btn_rec.setChecked(True)
    assert window.record_count_label.text() == "3"
    assert not window.engine.recording
    for instant, label in ((100.51, "2"), (101.01, "1")):
        now[0] = instant
        window._advance_record_count()
        assert window.record_count_label.text() == label
        assert not window.engine.recording
        assert not starts
    now[0] = 101.51
    window._advance_record_count()
    assert window.engine.recording
    assert starts == [True]
    assert window.record_count_label.isHidden()
    window._advance_record_count()
    assert starts == [True]


@pytest.mark.parametrize("cancel", ["record", "stop", "mode", "play"])
def test_cancel_count_in_never_starts_recording(window, monkeypatch, cancel):
    starts = []
    monkeypatch.setattr(window.engine, "play", lambda: starts.append(True))
    window.btn_rec.setChecked(True)
    if cancel == "record":
        window.btn_rec.setChecked(False)
    elif cancel == "stop":
        window.stop_all()
    elif cancel == "mode":
        window.set_mode("song")
    else:
        window.toggle_play()
    window._advance_record_count()
    assert not starts
    assert not window.engine.recording
    assert not window.btn_rec.isChecked()
    assert window.record_count_label.isHidden()
    assert not window._record_count_timer.isActive()
