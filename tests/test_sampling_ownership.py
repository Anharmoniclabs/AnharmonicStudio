"""Sound printing and held-note releases retain their selected source."""

from dataclasses import replace

import numpy as np
import pytest

from mpclab.synth import render_patch
from mpclab.ui import window_sampling
from test_track_recording import arm, window as window


def test_print_uses_selected_native_patch_and_restores_pad_with_undo(window):
    original = replace(window.project.synth)
    selected = window.project.add_instrument(
        "Second instrument", replace(original, name="Selected lead", osc1="square", release=0.01)
    )
    window.project.selected_instrument = selected.id
    window.select_pad(3)
    before = window.project.to_dict()
    expected = render_patch(
        replace(selected.patch),
        window.synth_panel.base_note,
        120 / window.project.bpm,
        window.engine.sr,
    )

    window.print_synth_to_pad()

    pad = window.project.pads[3]
    assert pad.name.startswith("Selected lead")
    np.testing.assert_allclose(window.library.audio(pad.sample_id), expected, atol=1e-6)
    assert window.project.synth == original
    assert len(window._undo) == 1
    window.undo()
    assert window.project.to_dict() == before
    window.redo()
    assert window.project.pads[3].sample_id == pad.sample_id


@pytest.mark.parametrize("selected", [False, True])
def test_external_print_explains_limit_without_history_or_library_mutation(
    window, monkeypatch, selected
):
    spec = {"path": "/missing/synthetic.vst3"}
    if selected:
        instrument = window.project.add_instrument("External", window.project.synth)
        instrument.plugin = spec
        window.project.selected_instrument = instrument.id
    else:
        window.project.plugins["instrument"] = spec
    before = window.project.to_dict()
    clips = dict(window.library.clips)
    monkeypatch.setattr(
        window_sampling, "render_patch", lambda *_: pytest.fail("Wrong native sound rendered")
    )

    window.print_synth_to_pad()

    assert "External instruments cannot be printed" in window.status.currentMessage()
    assert window.project.to_dict() == before
    assert window.library.clips == clips
    assert not window._undo


@pytest.mark.parametrize("stage", ["render", "save"])
def test_failed_print_preserves_pad_and_undo_redo(window, monkeypatch, stage):
    window.snapshot()
    window.project.name = "A reversible change"
    window.undo()
    window._set_dirty(False)
    before = window.project.to_dict()
    history = list(window._undo), list(window._redo)

    def fail(*_args, **_kwargs):
        raise OSError("Synthetic failure")

    if stage == "render":
        monkeypatch.setattr(window_sampling, "render_patch", fail)
    else:
        monkeypatch.setattr(
            window_sampling, "render_patch", lambda *_: np.zeros((128, 2), np.float32)
        )
        monkeypatch.setattr(window.library, "add_audio", fail)
    window.print_synth_to_pad()

    assert window.project.to_dict() == before
    assert (window._undo, window._redo) == history
    assert not window._dirty
    assert "Synthetic failure" in window.status.currentMessage()


def test_original_synth_release_keeps_note_on_owner_after_selection_changes(window):
    row = arm(window, source="notes")
    other = window.project.add_instrument("Other", window.project.synth)
    window.btn_rec.click()
    window.engine._process_commands()
    window.engine.beat = 1.0
    window.sample_workflow.note_on(60, 0.7)
    window.project.selected_instrument = other.id
    window.engine.beat = 1.5
    window.sample_workflow.note_off(60)

    assert not window.track_capture.held
    assert [(n.instrument, n.duration) for n in window.track_capture.notes] == [(None, 0.5)]
    window.stop_all()
    assert len(row.clips) == 1


def test_pad_release_preserves_recorded_pitch_after_root_edit(window):
    row = arm(window, source="notes")
    source = window.library.add_audio(np.full((128, 2), 0.1, np.float32), "Synthetic pad")
    pad = window.project.pads[0]
    pad.sample_id, pad.end = source.id, source.duration
    window.btn_rec.click()
    window.engine._process_commands()
    window.engine.beat = 1.0
    window._pad_pressed(0, 0.6)
    pad.root_note = 72
    window.engine.beat = 1.5
    window._pad_released(0)

    assert not window.track_capture.held
    assert [(n.pitch, n.pad, n.duration) for n in window.track_capture.notes] == [(60, 0, 0.5)]
    window.stop_all()
    assert len(row.clips) == 1
