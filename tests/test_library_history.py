"""Durable, recoverable library transactions and project-history integration."""

from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf

from mpclab.engine import Engine
from mpclab.library import Library, LibraryHistoryError
from mpclab.ui import main_window
from mpclab.ui.main_window import MainWindow


class _MemorySettings:
    IniFormat = object()
    UserScope = object()
    values = {"audio/setup_complete": True}

    def __init__(self, *_args, **_kwargs):
        pass

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def test_create_rename_and_trash_round_trip_across_restart(tmp_path):
    root = tmp_path / "library"
    library = Library(root, sample_rate=8_000)
    base = library.journal.cursor
    clip = library.add_audio(np.zeros((80, 2), dtype=np.float32), "Take one")
    created = library.journal.cursor
    library.rename(clip.id, "Take final")
    renamed = library.journal.cursor
    moved = library.delete(clip.id)
    trashed = library.journal.cursor

    assert moved is not None and moved.is_dir()
    reopened = Library(root, sample_rate=8_000)
    assert clip.id not in reopened.clips

    reopened.journal.restore(renamed)
    assert reopened.clips[clip.id].name == "Take final"
    reopened.journal.restore(created)
    assert reopened.clips[clip.id].name == "Take one"
    reopened.journal.restore(base)
    assert clip.id not in reopened.clips
    assert reopened.journal.trash_root.is_dir()

    reopened.journal.restore(trashed)
    assert clip.id not in reopened.clips
    assert Library(root, sample_rate=8_000).journal.cursor == trashed


def test_restore_refuses_to_trash_a_referenced_created_asset(tmp_path):
    library = Library(tmp_path / "library", sample_rate=8_000)
    base = library.journal.cursor
    clip = library.add_audio(np.zeros((8, 2), dtype=np.float32), "used")

    with pytest.raises(LibraryHistoryError, match="still references"):
        library.journal.restore(base, {clip.id})

    assert clip.id in library.clips
    assert library.folder(clip.id).is_dir()


def test_corrupt_journal_is_quarantined_without_losing_audio(tmp_path):
    root = tmp_path / "library"
    library = Library(root, sample_rate=8_000)
    clip = library.add_audio(np.zeros((8, 2), dtype=np.float32), "safe")
    library.journal.path.write_text('{"version":1,"entries":[{"trash":"../../escape"}]}')

    reopened = Library(root, sample_rate=8_000)

    assert clip.id in reopened.clips
    assert reopened.folder(clip.id).is_dir()
    assert list(root.glob(".library-history.corrupt-*"))
    assert reopened.journal.entries == []


def test_journal_is_bounded_without_deleting_recoverable_audio(tmp_path):
    library = Library(tmp_path / "library", sample_rate=8_000)
    clip = library.add_audio(np.zeros((8, 2), dtype=np.float32), "take")
    for index in range(85):
        library.rename(clip.id, f"take {index}")

    payload = json.loads(library.journal.path.read_text())
    assert len(library.journal.entries) == library.journal.limit == 80
    assert len(payload["entries"]) == 80
    assert payload["base"] is not None


def test_failed_journal_commit_rolls_rename_back(monkeypatch, tmp_path):
    library = Library(tmp_path / "library", sample_rate=8_000)
    clip = library.add_audio(np.zeros((8, 2), dtype=np.float32), "before")
    journal_before = library.journal.path.read_bytes()
    monkeypatch.setattr(library.journal, "_save", lambda: (_ for _ in ()).throw(OSError("disk")))

    with pytest.raises(OSError, match="disk"):
        library.rename(clip.id, "after")

    assert library.clips[clip.id].name == "before"
    assert json.loads((library.folder(clip.id) / "meta.json").read_text())["name"] == "before"
    assert library.journal.path.read_bytes() == journal_before


def test_import_undo_redo_survives_session_recovery(tmp_path, monkeypatch):
    _MemorySettings.values = {"audio/setup_complete": True}
    monkeypatch.setattr(main_window, "QSettings", _MemorySettings)
    monkeypatch.setattr(Engine, "start", lambda _engine, device=None: None)
    source = tmp_path / "outside.wav"
    sf.write(source, np.zeros((80, 2), dtype=np.float32), 8_000)

    first = MainWindow(tmp_path, restore_session=False)
    first.snapshot()
    clip = first.library.import_file(source)
    first._autosave_session()
    first.engine.stream = None
    first.close()

    monkeypatch.setattr(MainWindow, "_choose_session_recovery", lambda *_args: "restore")
    recovered = MainWindow(tmp_path)
    try:
        assert clip.id in recovered.library.clips
        recovered.undo()
        assert clip.id not in recovered.library.clips
        assert not recovered.library.folder(clip.id).exists()
        recovered.redo()
        assert clip.id in recovered.library.clips
        assert recovered.library.wav_path(clip.id).is_file()
    finally:
        recovered.engine.stream = None
        recovered.close()


def test_gui_history_round_trips_vocal_rename_duplicate_and_trash(tmp_path, monkeypatch):
    _MemorySettings.values = {"audio/setup_complete": True}
    monkeypatch.setattr(main_window, "QSettings", _MemorySettings)
    monkeypatch.setattr(Engine, "start", lambda _engine, device=None: None)
    window = MainWindow(tmp_path, restore_session=False)
    try:
        clip = window.library.add_audio(np.zeros((80, 2), dtype=np.float32), "lead", kind="vocal")
        window.snapshot()
        window.library.rename(clip.id, "lead final")
        window.undo()
        assert window.library.clips[clip.id].name == "lead"
        window.redo()
        assert window.library.clips[clip.id].name == "lead final"

        window.snapshot()
        duplicate = window.library.add_audio(
            window.library.audio(clip.id).copy(),
            "lead copy",
            kind="vocal",
        )
        window.undo()
        assert duplicate.id not in window.library.clips
        window.redo()
        assert duplicate.id in window.library.clips

        window.snapshot()
        window.library.delete(duplicate.id)
        window.undo()
        assert duplicate.id in window.library.clips
        window.redo()
        assert duplicate.id not in window.library.clips
    finally:
        window.engine.stream = None
        window.close()
