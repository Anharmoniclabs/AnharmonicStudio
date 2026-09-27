"""Prism controls must follow the selected independent instrument."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from mpclab.model import SynthPatch
from mpclab.plugin_registry import validate_plugin_spec
from tests.test_product_hardening_ui import window  # noqa: F401


def owned(window):  # noqa: F811
    instance = window.project.add_instrument("Second Prism", SynthPatch(track=3))
    window.project.selected_instrument = instance.id
    return instance.id


def test_host_diagnostics_can_resolve_instrument_owner(window):  # noqa: F811
    instrument_id = owned(window)
    assert window.project.instrument(instrument_id).name == "Second Prism"
    assert window.project.instrument_patch(instrument_id).track == 3
    with pytest.raises(ValueError, match="unknown instrument"):
        window.project.instrument("missing")


def test_unchanged_prism_poll_does_not_reapply_styles(window, monkeypatch):  # noqa: F811
    editor = window.synth_panel.prism_surface
    editor.sync()
    calls = []
    monkeypatch.setattr(editor, "setStyleSheet", calls.append)
    editor.sync()
    editor.sync()
    assert calls == []


def test_prism_parameter_changes_do_not_restyle_the_editor(window, monkeypatch):  # noqa: F811
    editor = window.synth_panel.prism_surface
    editor.sync()
    original = dict(editor.theme)
    calls = []
    monkeypatch.setattr(editor, "setStyleSheet", calls.append)
    for value in (0.1, 0.4, 0.8):
        editor.values.update({"12": value, "56": value, "57": value})
        editor.apply_theme()
    assert editor.theme == original
    assert calls == []


def test_duplicate_and_remove_keep_hosted_sound_ownership(window, monkeypatch):  # noqa: F811
    from mpclab.ui.instruments import edit_instrument

    instrument_id = owned(window)
    specification = validate_plugin_spec(
        {"path": "/tmp/Anharmonic Prism.vst3", "parameters": {"12": 0.8}}
    )
    window.project.instrument_plugins[instrument_id] = deepcopy(specification)
    monkeypatch.setattr(window.devices, "sync_project", lambda: None)
    duplicate_id = edit_instrument(window, "duplicate", name="Copy")
    assert window.project.instrument_plugins[duplicate_id] == specification
    window.project.instrument_plugins[duplicate_id]["parameters"]["12"] = 0.3
    assert window.project.instrument_plugins[instrument_id] == specification
    edit_instrument(window, "remove")
    assert duplicate_id not in window.project.instrument_plugins
    assert window.project.instrument_plugins[instrument_id] == specification


def test_duplicate_original_prism_copies_its_plugin(window, monkeypatch):  # noqa: F811
    from mpclab.ui.instruments import edit_instrument

    specification = validate_plugin_spec(
        {"path": "/tmp/Anharmonic Prism.vst3", "parameters": {"12": 0.4}}
    )
    window.project.plugins["instrument"] = deepcopy(specification)
    monkeypatch.setattr(window.devices, "sync_project", lambda: None)
    duplicate_id = edit_instrument(window, "duplicate", name="Copy original")
    assert window.project.instrument_plugins[duplicate_id] == specification
    assert window.project.plugins["instrument"] == specification


def test_ab_comparison_does_not_apply_another_instruments_sound(window, monkeypatch):  # noqa: F811
    first = owned(window)
    second = window.project.add_instrument("Third Prism", SynthPatch(track=4)).id
    for instrument_id, cutoff in ((first, 0.2), (second, 0.8)):
        window.project.instrument_plugins[instrument_id] = {
            "path": "/tmp/Anharmonic Prism.vst3",
            "parameters": {"12": cutoff},
        }
    bridge = SimpleNamespace(info={}, set_parameters=lambda values: None)
    monkeypatch.setattr(window.engine.external, "instrument_for", lambda key=None: bridge)
    editor = window.synth_panel.prism_surface
    editor.sync()
    editor.store()
    editor.change({"12": 0.3})
    window.project.selected_instrument = second
    editor.sync()
    editor.swap()
    assert editor.values["12"] == 0.8
    assert window.project.instrument_plugins[second]["parameters"]["12"] == 0.8
    window.project.selected_instrument = first
    editor.sync()
    editor.swap()
    assert editor.values["12"] == 0.2


def test_launch_prism_targets_selected_instance(window, monkeypatch):  # noqa: F811
    instrument_id = owned(window)
    calls = []
    monkeypatch.setattr("mpclab.prism.bundled_plugin", lambda: "/tmp/Anharmonic Prism.vst3")
    monkeypatch.setattr(window.devices, "load_plugin", lambda *a, **kw: calls.append((a, kw)))
    window.synth_panel._use_prism()
    assert calls[0][1].get("instrument_id") != instrument_id
    assert window.project.selected_instrument in window.project.pattern().instrument_ids


def test_prism_controls_edit_and_display_only_selected_instance(window, monkeypatch):  # noqa: F811
    instrument_id = owned(window)
    original = {"path": "/tmp/Anharmonic Prism.vst3", "parameters": {"12": 0.2}}
    window.project.plugins["instrument"] = deepcopy(original)
    window.project.instrument_plugins[instrument_id] = {
        "path": original["path"],
        "parameters": {"12": 0.8},
    }
    changed = []
    bridge = SimpleNamespace(info={}, set_parameters=changed.append)
    monkeypatch.setattr(window.engine.external, "instrument_for", lambda key=None: bridge)
    panel = window.synth_panel
    assert panel._external_selected()
    panel.sync_plugin_mode()
    assert panel.instrument_tabs.currentIndex() == 1
    assert panel.prism_surface.values["12"] == pytest.approx(0.8)
    panel.prism_surface.change({"12": 0.65})
    assert changed == [{"12": 0.65}]
    assert window.project.instrument_plugins[instrument_id]["parameters"]["12"] == 0.65
    assert window.project.plugins["instrument"] == original
    from mpclab.ui.prism_controls import prism_theme

    panel.prism_surface.sync()
    assert panel.prism_surface.theme == prism_theme(panel.prism_surface.values)


def test_builtin_and_prism_insert_without_replacing_existing_sound(window, monkeypatch):  # noqa: F811
    instrument_id = owned(window)
    specification = {"path": "/tmp/Anharmonic Prism.vst3", "parameters": {"12": 0.8}}
    window.project.instrument_plugins[instrument_id] = deepcopy(specification)
    removed, loaded = [], []
    monkeypatch.setattr(window.devices, "remove_plugin", lambda *a, **kw: removed.append((a, kw)))
    monkeypatch.setattr(window.devices, "load_plugin", lambda *a, **kw: loaded.append((a, kw)))
    monkeypatch.setattr("mpclab.prism.bundled_plugin", lambda: specification["path"])
    panel = window.synth_panel
    panel._use_builtin()
    native_id = window.project.selected_instrument
    assert native_id != instrument_id and not removed
    panel._use_prism()
    prism_id = window.project.selected_instrument
    assert prism_id not in (native_id, instrument_id)
    assert loaded[-1][1]["instrument_id"] == prism_id
    assert window.project.instrument_plugins[instrument_id] == specification
