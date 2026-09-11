from __future__ import annotations

import json

import pytest

from mpclab.model import Project
from mpclab import project_io


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
