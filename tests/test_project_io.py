from __future__ import annotations

import json

import pytest

from mpclab import model as model_module
from mpclab import project_io
from mpclab.model import Project


def test_bounded_project_loader_roundtrips_saved_project(tmp_path):
    path = tmp_path / "project.json"
    project = Project(name="bounded")
    project.bpm = 123
    project.save(path)

    restored = project_io.load_project_file(path)
    assert restored.name == "bounded"
    assert restored.bpm == 123


def test_bounded_project_loader_rejects_oversized_input_before_json_parse(tmp_path, monkeypatch):
    path = tmp_path / "huge.json"
    path.write_bytes(b"{" + b" " * 128)
    monkeypatch.setattr(project_io, "MAX_PROJECT_FILE_BYTES", 32)

    with pytest.raises(ValueError, match="project file exceeds the 32 bytes safety limit"):
        project_io.load_project_file(path)


def test_bounded_project_loader_reports_invalid_utf8(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_bytes(b"{\xff}")

    with pytest.raises(ValueError, match="project file must be UTF-8 JSON"):
        project_io.load_project_file(path)


def test_bounded_project_loader_wraps_json_error_with_location(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"bpm": 120,,}', encoding="utf-8")

    with pytest.raises(ValueError, match=r"project file is not valid JSON \(line 1, column"):
        project_io.load_project_file(path)


def test_bounded_history_loader_rejects_oversized_history(tmp_path, monkeypatch):
    path = tmp_path / ".project.json.history"
    path.write_text(json.dumps({"undo": ["x" * 200]}), encoding="utf-8")
    monkeypatch.setattr(project_io, "MAX_HISTORY_FILE_BYTES", 64)

    with pytest.raises(ValueError, match="undo history exceeds the 64 bytes safety limit"):
        project_io.load_history_file(path)


def test_atomic_save_keeps_previous_project_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "project.json"
    Project(name="old").save(path)
    previous = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(model_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        Project(name="new").save(path)

    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".project.json.*.tmp"))


def test_atomic_save_cleans_temporary_file_when_fsync_fails(tmp_path, monkeypatch):
    path = tmp_path / "project.json"

    def fail_fsync(_fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(model_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated fsync failure"):
        Project(name="unsaved").save(path)

    assert not path.exists()
    assert not list(tmp_path.glob(".project.json.*.tmp"))
