"""Pattern-owned insertions must not replace sounds or interrupt other voices."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from mpclab.model import Pattern, Project
from mpclab.music import Note
from mpclab.ui.instruments import insert_pattern_instrument
from tests.test_product_hardening_ui import window  # noqa: F401


def test_insert_and_layer_persist_separate_pattern_owners(window, monkeypatch):  # noqa: F811
    monkeypatch.setattr("mpclab.prism.bundled_plugin", lambda: "/tmp/Anharmonic Prism.vst3")
    loads = []
    monkeypatch.setattr(window.devices, "load_plugin", lambda *args, **kwargs: loads.append(kwargs))
    first_pattern = window.project.pattern()
    native = insert_pattern_instrument(window, "native")
    first_pattern.notes = [Note(60, 0, 2, 0.7, instrument=native)]
    prism = insert_pattern_instrument(window, "prism", layer_notes=True)
    assert [note.instrument for note in first_pattern.notes] == [native, prism]
    assert first_pattern.notes[0] is not first_pattern.notes[1]
    spec = deepcopy(window.project.instrument_plugins[prism])
    second_pattern = Pattern(name="Second part")
    window.project.patterns.append(second_pattern)
    window.project.current_pattern = second_pattern.id
    second_prism = insert_pattern_instrument(window, "prism")
    assert len({native, prism, second_prism}) == 3
    window.project.instrument_plugins[second_prism]["parameters"]["12"] = 0.1
    assert window.project.instrument_plugins[prism] == spec
    assert [item["instrument_id"] for item in loads] == [prism, second_prism]
    restored = Project.from_dict(window.project.to_dict())
    assert restored.patterns[0].instrument_ids == [native, prism]
    assert restored.patterns[1].instrument_ids == [second_prism]
    window.project.current_pattern = first_pattern.id
    window._sync_pattern_controls()
    assert window.project.selected_instrument == prism
    window.project.current_pattern = second_pattern.id
    window._sync_pattern_controls()
    assert window.project.selected_instrument == second_prism


def test_native_preset_and_insertion_preserve_other_held_voice(window):  # noqa: F811
    first = insert_pattern_instrument(window, "native")
    window.engine.synth_note_on(60, 0.7, instrument_id=first)
    output = np.zeros((256, 2), np.float32)
    window.engine._callback(output, 256, None, False)
    voice = window.engine.synth_voices[0]
    window.engine.play(0)
    second = insert_pattern_instrument(window, "native")
    window.synth_panel._commit_preset("Copper Pluck")
    window.engine._callback(output, 256, None, False)
    assert window.engine.playing
    assert not voice.dead and voice.stage != "release"
    assert np.max(np.abs(output)) > 0
    assert window.project.instrument_patch(first) is not window.project.instrument_patch(second)


def test_plugin_completion_does_not_panic_or_steal_selection(window, monkeypatch):  # noqa: F811
    first = insert_pattern_instrument(window, "native")
    second = insert_pattern_instrument(window, "native")
    controller = window.devices
    key = controller._plugin_key("instrument", first)
    controller._slot_generation[key] = 42
    monkeypatch.setattr(window, "panic_synth", lambda: pytest.fail("global panic"))
    bridge = SimpleNamespace(info={"name": "Prism"}, blocksize=256, close=lambda: None)
    controller._plugin_loaded(
        42, "instrument", bridge, ({"path": "/tmp/Anharmonic Prism.vst3"}, True, first, key), ""
    )
    assert window.project.selected_instrument == second
    assert window.engine.external.instrument_for(first) is bridge


def test_pattern_instrument_references_are_validated():
    project = Project()
    project.pattern().instrument_ids = ["missing"]
    with pytest.raises(ValueError, match="pattern instruments"):
        project.to_dict()


def test_duplicate_pattern_clones_instruments_and_plugin_specs(window, monkeypatch):  # noqa: F811
    monkeypatch.setattr("mpclab.prism.bundled_plugin", lambda: "/tmp/Anharmonic Prism.vst3")
    monkeypatch.setattr(window.devices, "load_plugin", lambda *args, **kwargs: None)
    owner = insert_pattern_instrument(window, "prism")
    source = window.project.pattern()
    source.notes = [Note(64, 0, 2, 0.8, instrument=owner)]
    window.dup_pattern()
    duplicate = window.project.pattern()
    copied_owner = duplicate.notes[0].instrument
    assert copied_owner != owner
    assert duplicate.instrument_ids == [copied_owner]
    window.project.instrument_plugins[copied_owner]["parameters"]["12"] = 0.01
    assert (
        window.project.instrument_plugins[owner] != window.project.instrument_plugins[copied_owner]
    )
    assert source.notes[0].instrument == owner
    Project.from_dict(window.project.to_dict())


def test_async_native_preset_keeps_original_owner(window):  # noqa: F811
    first = insert_pattern_instrument(window, "native")
    panel = window.synth_panel
    panel._loading_owner = first
    panel._load_request = 123
    second = insert_pattern_instrument(window, "native")
    before = deepcopy(window.project.instrument_patch(second))
    panel._instrument_ready(123, "Copper Pluck", window.project, "")
    assert window.project.instrument_patch(first).name == "Copper Pluck"
    assert window.project.instrument_patch(second) == before
    assert window.project.selected_instrument == second
