"""Ordinary history moves preserve audio resources and commit atomically."""

from types import SimpleNamespace

import numpy as np
import pytest

from mpclab.music import Note
from mpclab.plugin_registry import validate_plugin_spec
from tests.test_product_hardening_ui import window  # noqa: F401


def test_note_undo_preserves_samples_hosts_and_unchanged_note_identity(window, monkeypatch):  # noqa: F811
    clip = window.library.add_audio(np.ones((4800, 2), np.float32) * 0.1, "Drum")
    window.project.pads[0].sample_id = clip.id
    cached = window.library.audio(clip.id)
    pattern = window.project.pattern()
    held = Note(60, 0, 4, 0.7)
    pattern.notes = [held]
    window.snapshot()
    pattern.notes.append(Note(64, 1, 1, 0.6))
    for obj, name in (
        (window.library, "scan"),
        (window.devices, "sync_project"),
        (window, "apply_theme"),
        (window.engine, "reset_fx"),
        (window.engine, "synth_panic"),
        (window.engine, "sample_panic"),
        (window.engine, "preload_project_audio"),
    ):
        monkeypatch.setattr(obj, name, lambda *a, **kw: pytest.fail("unrelated resource rebuilt"))
    window.engine.playing = True
    window.engine.beat = 1.25
    window.undo()
    assert window.project.pattern() is pattern
    assert pattern.notes == [held] and pattern.notes[0] is held
    assert window.library.audio(clip.id) is cached
    assert window.engine.playing and window.engine.beat == 1.25
    window.redo()
    assert pattern.notes[0] is held and len(pattern.notes) == 2


def test_plugin_parameter_undo_updates_existing_host(window):  # noqa: F811
    spec = validate_plugin_spec({"path": "/tmp/Fixture.vst3", "parameters": {"gain": 0.2}})
    window.project.plugins["instrument"] = spec
    changes = []
    bridge = SimpleNamespace(
        error="", info={"parameters": {}}, set_parameters=changes.append, close=lambda: None
    )
    window.engine.external.instrument = bridge
    window.snapshot()
    spec["parameters"]["gain"] = 0.8
    window.undo()
    assert window.engine.external.instrument is bridge
    assert changes[-1] == {"gain": 0.2}
    window.redo()
    assert window.engine.external.instrument is bridge
    assert changes[-1] == {"gain": 0.8}


def test_failed_apply_keeps_both_history_stacks(window, monkeypatch):  # noqa: F811
    window.snapshot()
    window.project.name = "Edited"
    undo, redo = list(window._undo), list(window._redo)

    def fail(project):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(window, "_apply_project", fail)
    window.undo()
    assert window._undo == undo and window._redo == redo
    assert window.project.name == "Edited"


def test_restore_does_not_round_high_precision_controls(window):  # noqa: F811
    window.project.master = 0.812345678
    window.project.swing = 12.3456
    window.snapshot()
    window.project.name = "Edit"
    window.undo()
    assert window.project.master == 0.812345678
    assert window.project.swing == 12.3456


def test_partial_restore_failure_rolls_back_project_and_keeps_history(window, monkeypatch):  # noqa: F811
    window.snapshot()
    window.project.name = "Keep this edit"
    undo = list(window._undo)
    original = window._apply_project
    calls = []

    def fail_once(project):
        original(project)
        calls.append(project)
        if len(calls) == 1:
            raise RuntimeError("UI refresh failed")

    monkeypatch.setattr(window, "_apply_project", fail_once)
    window.undo()
    assert window.project.name == "Keep this edit"
    assert window._undo == undo and not window._redo


def test_undo_redo_instrument_insert_restores_live_input_route(window):  # noqa: F811
    from mpclab.ui.instruments import insert_pattern_instrument

    owner = insert_pattern_instrument(window, "native")
    assert window.engine.midi.route[1] == owner
    window.undo()
    assert window.project.selected_instrument is None
    assert window.engine.midi.route[1] is None
    window.redo()
    assert window.project.selected_instrument == owner
    assert window.engine.midi.route[1] == owner
