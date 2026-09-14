"""Project replacement must commit paths/history only after DSP preparation succeeds."""

import json

import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from mpclab.application_features import attach_application_features, install_application_runtime
from mpclab.engine import Engine
from mpclab.model import Project
from mpclab.ui import main_window
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    assert QApplication.platformName() == "offscreen"
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self, device=None: None)
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda _parent, title, message: warnings.append((title, message))
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Discard)
    install_application_runtime()
    instance = main_window.MainWindow(tmp_path, restore_session=False)
    attach_application_features(instance)
    for timer in instance.findChildren(QTimer):
        timer.stop()
    try:
        yield instance, warnings
    finally:
        instance._dirty = False
        instance.close()  # only this disposable offscreen test window


def _saved_session(window, track_count=8, *, dirty=True):
    if track_count != len(window.project.tracks):
        project = Project.from_dict(window.project.to_dict())
        while len(project.tracks) < track_count:
            project.add_track()
        window._apply_project(project)
    clip = window.library.add_audio(np.full((480, 2), 0.125, np.float32), "Current sample")
    window.project.pads[0].sample_id = clip.id
    window.project.pads[0].end = clip.duration
    window.project.pads[0].track = track_count - 1
    window.load_clip_into_editor(clip.id)
    window.proj_name.setText("Saved working project")
    path = window.projects_dir / "original.json"
    assert window._save_project_to(path)
    earlier = Project.from_dict(window.project.to_dict())
    earlier.name = "Earlier undo state"
    later = Project.from_dict(window.project.to_dict())
    later.name = "Later redo state"
    window._undo = [window._history_state(earlier)]
    window._redo = [window._history_state(later)]
    window._save_history()
    if dirty:
        window.proj_name.setText("Unsaved working project")
    window._set_dirty(dirty)
    return path


def _incoming_project(window):
    project = Project(name="Incoming nine-track project", bpm=143)
    project.add_track("Independent ninth track")
    project.tracks[8].gain = 0.37
    project.synth.track = 8
    path = window.projects_dir / "incoming.json"
    project.save(path)
    undo_project = Project.from_dict(project.to_dict())
    undo_project.name = "Incoming project's undo state"
    payload = {"version": 2, "undo": [{"project": undo_project.to_dict()}], "redo": []}
    history = window._project_history_path(path)
    history.write_text(json.dumps(payload), encoding="utf-8")
    return path, history, payload


def _snapshot(window, paths):
    return {
        "project": window.project,
        "model": window.project.to_dict(),
        "path": window.project_path,
        "history_path": window.history_path,
        "undo": list(window._undo),
        "redo": list(window._redo),
        "dirty": window._dirty,
        "title": window.windowTitle(),
        "clip": window.current_clip,
        "wave_clip": window.wave.clip_id,
        "audio": window.wave.audio.copy(),
        "clip_label": window.clip_label.text(),
        "files": {path: path.read_bytes() for path in paths},
    }


def _assert_preserved(window, before):
    assert window.project is before["project"]
    assert window.engine.project is before["project"]
    assert window.project.to_dict() == before["model"]
    assert len(window.engine._tbuf) == len(window.project.tracks) == len(window.mixer.strips)
    assert window.project_path == before["path"]
    assert window.history_path == before["history_path"]
    assert window._undo == before["undo"]
    assert window._redo == before["redo"]
    assert window._dirty is before["dirty"]
    assert window.windowTitle() == before["title"]
    assert window.current_clip == before["clip"]
    assert window.wave.clip_id == before["wave_clip"]
    np.testing.assert_array_equal(window.wave.audio, before["audio"])
    assert window.clip_label.text() == before["clip_label"]
    for path, content in before["files"].items():
        assert path.read_bytes() == content, path
    assert window.engine.stream is None


def _fail_target_preparation(window, monkeypatch, track_count, *, after_allocation):
    prepare = window.engine.prepare_fx
    attempts = []

    def fail(project=None):
        requested = project if project is not None else window.engine.project
        count = len(requested.tracks)
        attempts.append(count)
        if count == track_count and not after_allocation:
            raise RuntimeError("Simulated mixer preparation failure")
        result = prepare(project)
        if count == track_count:
            assert len(window.engine._tbuf) == track_count
            raise RuntimeError("Simulated mixer preparation failure")
        return result

    monkeypatch.setattr(window.engine, "prepare_fx", fail)
    return attempts


@pytest.mark.parametrize("after_allocation", [False, True])
@pytest.mark.parametrize("dirty", [False, True])
def test_failed_expanded_load_preserves_current_session_and_both_sources(
    window, monkeypatch, after_allocation, dirty
):
    app, warnings = window
    original = _saved_session(app, dirty=dirty)
    incoming, incoming_history, _ = _incoming_project(app)
    app.project.save(app.session_path)
    app._save_history(app.session_history_path)
    before = _snapshot(
        app,
        [
            original,
            app.history_path,
            incoming,
            incoming_history,
            app.session_path,
            app.session_history_path,
        ],
    )
    loaded_history = []
    monkeypatch.setattr(app, "_load_history", loaded_history.append)
    attempts = _fail_target_preparation(app, monkeypatch, 9, after_allocation=after_allocation)

    assert app.load_project_path(incoming) is False

    assert attempts == [9, 8]
    assert loaded_history == []
    assert warnings == [("Load failed", "Simulated mixer preparation failure")]
    _assert_preserved(app, before)


@pytest.mark.parametrize("clear_session", [False, True])
def test_successful_expanded_load_commits_new_paths_and_history_after_preparation(
    window, monkeypatch, clear_session
):
    app, warnings = window
    original = _saved_session(app)
    incoming, incoming_history, payload = _incoming_project(app)
    app.project.save(app.session_path)
    app._save_history(app.session_history_path)
    source_bytes = {
        path: path.read_bytes()
        for path in (
            original,
            app.history_path,
            incoming,
            incoming_history,
        )
    }
    recovery_bytes = {
        path: path.read_bytes()
        for path in (
            app.session_path,
            app.session_history_path,
        )
    }
    load_history = app._load_history
    calls = []

    def load_after_commit(path):
        assert app.project_path == incoming
        assert app.history_path == incoming_history
        assert app.project.name == "Incoming nine-track project"
        assert app.engine.project is app.project
        assert len(app.engine._tbuf) == len(app.mixer.strips) == 9
        calls.append(path)
        load_history(path)

    monkeypatch.setattr(app, "_load_history", load_after_commit)

    assert app.load_project_path(incoming, clear_session=clear_session) is True

    assert calls == [incoming_history]
    assert [json.loads(item) for item in app._undo] == payload["undo"]
    assert app._redo == []
    assert app._dirty is False
    assert app.project.synth.track == 8
    assert app.project.tracks[8].gain == pytest.approx(0.37)
    assert warnings == []
    for path, content in source_bytes.items():
        assert path.read_bytes() == content, path
    for path, content in recovery_bytes.items():
        if clear_session:
            assert not path.exists()
        else:
            assert path.read_bytes() == content
    assert app.engine.stream is None


@pytest.mark.parametrize("after_allocation", [False, True])
def test_failed_new_default_project_preserves_expanded_session_and_sample(
    window, monkeypatch, after_allocation
):
    app, warnings = window
    original = _saved_session(app, track_count=9)
    before = _snapshot(app, [original, app.history_path])
    attempts = _fail_target_preparation(app, monkeypatch, 8, after_allocation=after_allocation)

    assert app.new_project() is False

    assert attempts == [8, 9]
    assert warnings == [("New project failed", "Simulated mixer preparation failure")]
    _assert_preserved(app, before)
