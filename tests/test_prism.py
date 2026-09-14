"""Sound exchange, presets, editor history and rendered visualization regressions."""

from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from mpclab.model import SynthPatch, ArpSettings
from mpclab.prism import ARP_PRESETS, parse_sound, sound_document, mutate_patch
from mpclab.synth import PATCHES, render_patch
from mpclab.instrument_state import validate_patch
from tests.test_product_hardening_ui import window  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_factory_bank_is_identical_to_studio():
    bank = json.loads((ROOT / "plugins/prism/factory.json").read_text())
    assert len(bank) == 54
    for item in bank:
        assert item["patch"] == asdict(PATCHES[item["name"]])
        validate_patch(PATCHES[item["name"]])


@pytest.mark.parametrize("entry", json.loads((ROOT / "mpclab/prism_expansion.json").read_text()))
def test_new_sounds_render_finite_non_silent_audio(entry):
    patch = PATCHES[entry["name"]]
    audio = render_patch(patch, 60, 0.2, 24000)
    assert np.isfinite(audio).all()
    assert np.max(np.abs(audio)) > 0.001
    assert np.max(np.abs(audio)) <= 0.981


@pytest.mark.parametrize("arp", ARP_PRESETS)
def test_sound_and_arp_exchange_roundtrip(arp):
    settings = ArpSettings(**{k: v for k, v in arp.items() if k != "name"})
    patch = PATCHES["Prism Droplets"]
    actual, result = parse_sound(json.dumps(sound_document(patch, settings)))
    assert asdict(actual) == asdict(patch)
    assert result == settings


@pytest.mark.parametrize(
    "change",
    [{"cutoff": float("nan")}, {"sample_source": "nope"}, {"unknown": 1}, {"osc1": "missing"}],
)
def test_invalid_sound_cannot_reach_engine(change):
    doc = sound_document(SynthPatch(), ArpSettings())
    doc["patch"].update(change)
    with pytest.raises(ValueError):
        parse_sound(json.dumps(doc))


def test_mutation_is_bounded_and_does_not_change_level_or_timing():
    patch = SynthPatch()
    for seed in range(100):
        new = mutate_patch(patch, seed)
        validate_patch(new)
        assert new != patch
        assert new.volume == patch.volume
        assert new.attack == patch.attack
        assert new.track == patch.track


def test_sound_tools_history_and_ab(window):  # noqa: F811
    panel = window.synth_panel
    panel._store_a()
    before = asdict(window.project.selected_patch)
    history = len(window._undo)
    panel._mutate_tone()
    assert len(window._undo) == history + 1
    after = asdict(window.project.selected_patch)
    assert after != before
    panel._swap_a()
    assert asdict(window.project.selected_patch) == before
    panel._swap_a()
    assert asdict(window.project.selected_patch) == after
    panel._arp_preset(1)
    assert window.project.arp.enabled
    assert window.project.arp.octaves == 2
    panel._effect_preset("Prism echoes")
    assert window.project.tracks[window.project.selected_patch.track].fx.send_delay == 0.32


def test_scope_renders_and_does_not_change_project(window):  # noqa: F811
    panel = window.synth_panel
    before = window.project.to_dict()
    for _ in range(100):
        QApplication.processEvents()
        if panel.visualizer._data is not None:
            break
        QTest.qWait(20)
    assert panel.visualizer._data is not None
    assert window.project.to_dict() == before
    assert panel.visualizer._data[0].shape == (512,)


def test_plugin_parameter_mapping_matches_native_host_contract():
    from mpclab.ui.prism_controls import SPECS, normalized, plain, patch_parameters

    assert SPECS == json.loads((ROOT / "plugins/prism/parameters.json").read_text())
    for spec in SPECS:
        for value in (spec["low"], spec["default"], spec["high"]):
            assert plain(spec, normalized(spec, value)) == pytest.approx(value)
    mapped = patch_parameters(SynthPatch())
    assert len(mapped) == 21
    assert mapped["0"] == 0
    assert mapped["1"] == 1


def test_bundled_plugin_button_loads_current_tone_and_returns_to_builtin(window):  # noqa: F811
    from mpclab.prism import bundled_plugin
    from mpclab.ui.prism_controls import SPECS, normalized

    if bundled_plugin() is None:
        pytest.skip("Build Prism before running the bundled-plugin integration test")
    window.project.selected_patch.cutoff = 4321
    window.synth_panel._use_prism()
    for _ in range(200):
        QApplication.processEvents()
        if window.engine.external.instrument is not None:
            break
        QTest.qWait(25)
    assert window.engine.external.instrument is not None, window.devices.plugin_status
    assert not window.synth_panel._controls[0][0].isEnabled()
    before = asdict(window.project.selected_patch)
    assert window.synth_panel.load_preset("Carbon Pulse") is False
    assert asdict(window.project.selected_patch) == before
    spec = window.project.plugins["instrument"]
    assert spec["parameters"]["12"] == pytest.approx(normalized(SPECS[12], 4321))
    window.synth_panel._use_builtin()
    assert window.engine.external.instrument is None
    assert "instrument" not in window.project.plugins
    assert window.synth_panel._controls[0][0].isEnabled()


def test_graphical_editor_changes_running_plugin_without_reloading(window):  # noqa: F811
    from mpclab.prism import bundled_plugin
    from mpclab.ui.prism_controls import PrismKnob
    from PySide6.QtWidgets import QDoubleSpinBox

    if bundled_plugin() is None:
        pytest.skip("Build Prism first")
    panel = window.synth_panel
    panel._use_prism()
    for _ in range(200):
        QApplication.processEvents()
        if window.engine.external.instrument is not None:
            break
        QTest.qWait(25)
    bridge = window.engine.external.instrument
    assert bridge is not None
    pid = bridge.plugin.process.pid
    editor = panel.prism_surface
    assert len(editor.findChildren(PrismKnob)) == 26
    assert not editor.findChildren(QDoubleSpinBox)
    history = len(window._undo)
    knob = editor.knobs["12"]
    knob.setSliderDown(True)
    knob.setValue(6000)
    knob.setValue(7000)
    knob.setSliderDown(False)
    assert len(window._undo) == history + 1
    assert window.project.plugins["instrument"]["parameters"]["12"] == pytest.approx(0.7)
    import time

    for _ in range(100):
        if bridge.info["parameters"]["12"]["value"] == pytest.approx(0.7):
            break
        QApplication.processEvents()
        time.sleep(0.02)
    assert not bridge.error, (bridge.error, bridge.plugin.process.exitcode, bridge.plugin.closed)
    assert bridge.info["parameters"]["12"]["value"] == pytest.approx(0.7)
    assert not bridge.error, (bridge.error, bridge.plugin.process.exitcode, bridge.plugin.closed)
    assert bridge.plugin.process.pid == pid
    editor.store()
    editor.discrete("0", 2)
    editor.swap()
    assert editor.values["0"] == 0
    panel.instrument_tabs.setCurrentIndex(0)
    assert window.engine.external.instrument is None
    panel.instrument_tabs.setCurrentIndex(1)
    for _ in range(200):
        QApplication.processEvents()
        if window.engine.external.instrument is not None:
            break
        time.sleep(0.025)
    assert window.engine.external.instrument is not None
    assert window.project.plugins["instrument"]["parameters"]["12"] == pytest.approx(0.7)
    assert panel.instrument_tabs.currentIndex() == 1
    panel._use_builtin()


def test_live_parameters_preserve_held_note():
    from mpclab.prism import bundled_plugin
    from mpclab.plugin_host import IsolatedPlugin

    path = bundled_plugin()
    if path is None:
        pytest.skip("Build Prism first")
    plugin = IsolatedPlugin({"path": str(path)})
    try:
        plugin.render(None, 4096, [([0x90, 60, 100], 0)], reset=True)
        pid = plugin.process.pid
        plugin.set_parameters({"12": 0.85, "20": 0.6})
        after = plugin.render(None, 4096)
        assert np.max(np.abs(after)) > 0.001
        assert plugin.process.pid == pid
        plugin.set_parameters({"20": 0})
        plugin.render(None, 4096)
        quiet = plugin.render(None, 4096)
        assert np.max(np.abs(quiet)) < 0.0001
    finally:
        plugin.close()
