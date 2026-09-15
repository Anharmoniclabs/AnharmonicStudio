"""Regression probes for the V2 bug-bounty review."""

from dataclasses import replace
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from mpclab.autotune.shifter import Shifter, shift
from mpclab.model import VocalSettings
from mpclab.ui.vocal_note_editor import VocalNoteEditor
from mpclab.vocal import PitchAnalysis


@pytest.mark.parametrize(
    "spec", [{"sr": 192001}, {"sr": 384000}, {"sr": 7999}, {"channels": 3}, {"block": 0}]
)
def test_invalid_engine_spec_rejected_before_loading_native_library(monkeypatch, spec):
    def unexpected_load():
        pytest.fail("invalid specification reached native library")

    monkeypatch.setattr("mpclab.autotune.shifter.library", unexpected_load)
    with pytest.raises(ValueError):
        Shifter(**spec)


def test_closed_native_shifter_rejects_calls_instead_of_segfaulting():
    # Isolate native crash regressions from pytest and the user's applications.
    code = """import numpy as np
from mpclab.autotune.shifter import Shifter
s=Shifter()
s.close()
try:
    list(s.push(np.zeros((512,2),np.float32)))
except RuntimeError:
    pass
else:
    raise AssertionError("closed pitch engine accepted input")
"""
    process = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).parents[1], capture_output=True, timeout=10
    )
    assert process.returncode == 0, process.stderr.decode()


@pytest.mark.parametrize(
    "data",
    [
        np.zeros((2, 2, 2)),
        np.zeros(2),
        np.full((512, 2), np.nan),
        np.zeros((0, 2)),
        np.zeros((513, 2)),
    ],
)
def test_invalid_native_audio_rejected_before_ffi(data):
    engine = Shifter()
    try:
        with pytest.raises(ValueError):
            list(engine.push(data))
    finally:
        engine.close()


def test_retrieved_blocks_survive_native_close():
    engine = Shifter()
    result = []
    for _ in range(16):
        result = engine.push(np.ones((512, 2), np.float32) * 0.1)
        if result:
            break
    engine.close()
    assert result and all(np.isfinite(block).all() for block in result)


@pytest.mark.parametrize(
    "ratios,times",
    [([], []), ([1.0], [np.nan]), ([np.nan], [0.0]), ([1.0, 1.0], [1.0, 0.0]), ([1.0], [0.0, 1.0])],
)
def test_malformed_control_curves_rejected(ratios, times):
    with pytest.raises(ValueError):
        shift(np.zeros((512, 2), np.float32), ratios, times, 48000, 1.0)


@pytest.fixture
def editor():
    view = VocalNoteEditor()
    view.clip_id = "take"
    view.duration = 1.0
    view.analysis = PitchAnalysis(
        np.array([0.2, 0.3]),
        np.array([440.0, 440.0]),
        np.array([69.0, 69.0]),
        np.array([69.0, 69.0]),
        np.ones(2),
    )
    view.set_settings(
        VocalSettings(backend="v2", pitch_edits={"take": [dict(start=0.1, end=0.5, target=69.0)]})
    )
    view.selected = {0}
    yield view
    view.shutdown()


@pytest.mark.parametrize("key", [Qt.Key_S, Qt.Key_B, Qt.Key_J, Qt.Key_Delete, Qt.Key_Up])
def test_control_shortcuts_do_not_mutate_pitch_regions(editor, key):
    changes = []
    editor.editsChanged.connect(lambda *args: changes.append(args))
    QTest.keyClick(editor, key, Qt.ControlModifier)
    assert changes == []


def test_settings_change_invalidates_in_progress_drag(editor):
    editor._note_drag = (100, editor.notes())
    editor.set_settings(replace(editor.settings, transpose=12))
    assert editor._note_drag is None


def test_live_stalled_backend_has_bounded_dry_buffer(monkeypatch):
    from mpclab.autotune import live
    import time

    class Stalled:
        pad = delay = 0
        block = 512

        def __init__(self, *_):
            self.closed = False

        def push(self, *_):
            return []

        def close(self):
            self.closed = True

    monkeypatch.setattr(live, "Shifter", Stalled)
    output = []
    monitor = live.LiveMonitor(VocalSettings(), 48000, output.append)
    try:
        for _ in range(16):
            monitor.push(np.zeros((512, 2), np.float32))
            time.sleep(0.012)
            if monitor.failed:
                break
        assert monitor.failed
        assert "stopped returning" in monitor.error
        monitor.push(np.ones((512, 2), np.float32))
        assert np.all(output[-1] == 1)
    finally:
        monitor.close()
    assert monitor.shifter.closed


def test_failed_move_import_retains_original_source(tmp_path, monkeypatch):
    import soundfile as sf
    from mpclab.library import Library

    library = Library(tmp_path / "library")
    source = tmp_path / "recording.wav"
    sf.write(source, np.zeros((512, 2), np.float32), 48000)
    original = source.read_bytes()

    def fail(*_):
        raise OSError("simulated metadata write failure")

    monkeypatch.setattr(library, "_write_meta", fail)
    with pytest.raises(OSError, match="metadata"):
        library.import_file(source, move=True)
    assert source.is_file() and source.read_bytes() == original
    assert not library.clips


def test_successful_move_import_removes_source_only_after_commit(tmp_path, monkeypatch):
    import soundfile as sf
    from mpclab.library import Library

    library = Library(tmp_path / "library")
    source = tmp_path / "recording.wav"
    sf.write(source, np.zeros((512, 2), np.float32), 48000)
    commit = library.journal.commit

    def observed(entry):
        assert source.exists()
        return commit(entry)

    monkeypatch.setattr(library.journal, "commit", observed)
    clip = library.import_file(source, move=True)
    assert not source.exists()
    assert Library(library.root).clips[clip.id].id == clip.id


def test_short_pitch_analysis_cannot_create_reversed_region():
    from mpclab.autotune.targeting import notes_from_analysis

    analysis = PitchAnalysis(
        np.array([0.0213]), np.array([440.0]), np.array([69.0]), np.array([69.0]), np.ones(1)
    )
    assert notes_from_analysis(analysis, 0.005) == []


def test_move_import_sync_failure_retains_original_and_removes_partial_clip(tmp_path, monkeypatch):
    import soundfile as sf
    from mpclab import library as module

    library = module.Library(tmp_path / "library")
    source = tmp_path / "recording.wav"
    sf.write(source, np.zeros((512, 2), np.float32), 48000)
    original = source.read_bytes()

    def fail(_fd):
        raise OSError("simulated audio sync failure")

    monkeypatch.setattr(module.os, "fsync", fail)
    with pytest.raises(OSError, match="sync failure"):
        library.import_file(source, move=True)
    assert source.read_bytes() == original
    assert not library.clips
    assert not list(library.root.glob("*/audio.wav"))


def test_live_overload_never_calls_output_from_two_threads(monkeypatch):
    from mpclab.autotune import live
    import threading

    entered, release = threading.Event(), threading.Event()
    calls = []

    class Immediate:
        pad = delay = 0
        block = 512

        def __init__(self, *_):
            pass

        def push(self, block, *_):
            return [block]

        def close(self):
            pass

    monkeypatch.setattr(live, "Shifter", Immediate)

    def output(block):
        calls.append(threading.get_ident())
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)

    monitor = live.LiveMonitor(VocalSettings(), 48000, output)
    try:
        block = np.zeros((512, 2), np.float32)
        monitor.push(block)
        assert entered.wait(2)
        for _ in range(5):
            monitor.push(block)
        assert monitor.failed and len(calls) == 1
        release.set()
        monitor.worker.join(2)
        monitor.push(block)
        assert len(calls) == 2
    finally:
        release.set()
        monitor.close()
