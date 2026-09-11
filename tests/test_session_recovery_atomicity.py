from __future__ import annotations

import os
from pathlib import Path

import pytest

from mpclab.ui import session_history
from mpclab.ui.session_history import SessionHistoryMixin


class RecoveryHarness(SessionHistoryMixin):
    def __init__(self, root: Path):
        self.projects_dir = root
        self.session_path = root / ".session-autosave.json"
        self.session_history_path = root / ".session-history.json"


def test_archive_recovery_moves_project_and_history_as_a_pair(tmp_path):
    harness = RecoveryHarness(tmp_path)
    harness.session_path.write_text('{"name":"take"}', encoding="utf-8")
    harness.session_history_path.write_text('{"version":2}', encoding="utf-8")

    archived = harness._archive_session_recovery()
    archived_history = harness._project_history_path(archived)

    assert archived.read_text(encoding="utf-8") == '{"name":"take"}'
    assert archived_history.read_text(encoding="utf-8") == '{"version":2}'
    assert not harness.session_path.exists()
    assert not harness.session_history_path.exists()


def test_archive_recovery_rolls_project_back_if_history_move_fails(tmp_path, monkeypatch):
    harness = RecoveryHarness(tmp_path)
    project_bytes = b'{"name":"recover me"}'
    history_bytes = b'{"version":2,"undo":[]}'
    harness.session_path.write_bytes(project_bytes)
    harness.session_history_path.write_bytes(history_bytes)

    real_replace = os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated history archive failure")
        return real_replace(source, destination)

    monkeypatch.setattr(session_history.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="simulated history archive failure"):
        harness._archive_session_recovery()

    assert harness.session_path.read_bytes() == project_bytes
    assert harness.session_history_path.read_bytes() == history_bytes
    assert not list(tmp_path.glob("recovery-skipped-*.json"))


def test_archive_without_history_still_moves_project(tmp_path):
    harness = RecoveryHarness(tmp_path)
    harness.session_path.write_text('{"name":"project only"}', encoding="utf-8")

    archived = harness._archive_session_recovery()

    assert archived.exists()
    assert not harness.session_path.exists()
    assert not harness._project_history_path(archived).exists()
