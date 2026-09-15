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
    assert len(editor.findChildren(PrismKnob)) == 57
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


def test_workstation_performances_and_layer_controls(window):  # noqa: F811
    from mpclab.ui.prism_controls import CATALOG, PERFORMANCES, SPECS, plain
    from types import SimpleNamespace

    panel = window.synth_panel.prism_surface
    assert len(PERFORMANCES) == 120 and len(CATALOG) == 174
    edits = []
    window.project.plugins["instrument"] = {"path": "Anharmonic Prism.vst3", "parameters": {}}
    window.engine.external.instrument = SimpleNamespace(
        set_parameters=lambda v: edits.append(dict(v)),
        close=lambda: None,
        info={"parameters": {}},
        error="",
    )
    for item in PERFORMANCES:
        panel.preset(item["name"])
        assert len(edits[-1]) == 76
        assert all(0 <= v <= 1 for v in edits[-1].values())
        assert plain(SPECS[53], panel.values["53"]) > 0
    original = panel.values["12"]
    panel.presets.setCurrentRow(1)
    panel.load_layer(True)
    assert panel.values["12"] == original
    assert set(edits[-1]) == {str(i) for i in range(32, 53)}
    assert panel.edit_layer == 32
    panel.star_selected()
    panel.starred_only.setChecked(True)
    panel.filter_presets()
    assert sum(not panel.presets.item(i).isHidden() for i in range(panel.presets.count())) == 1
    window.engine.external.instrument = None


def test_workstation_sound_file_roundtrip_includes_layers_and_motion(window, tmp_path, monkeypatch):  # noqa: F811
    from mpclab.ui.prism_controls import QFileDialog, QMessageBox, PERFORMANCES

    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(str(args[-1])))
    from types import SimpleNamespace

    panel = window.synth_panel.prism_surface
    window.project.plugins["instrument"] = {"path": "Anharmonic Prism.vst3", "parameters": {}}
    window.engine.external.instrument = SimpleNamespace(
        set_parameters=lambda v: None, close=lambda: None, info={"parameters": {}}, error=""
    )
    panel.preset(PERFORMANCES[-1]["name"])
    saved = dict(panel.values)
    target = tmp_path / "full.prism.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    panel.save_sound()
    assert target.exists()
    panel.reset()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    panel.open_sound()
    assert panel.values == pytest.approx(saved)
    window.engine.external.instrument = None


def test_studio_tempo_reaches_real_prism_arpeggiator():
    from mpclab.prism import bundled_plugin
    from mpclab.plugin_host import IsolatedPlugin
    from mpclab.external_dsp import prism_tempo_events

    path = bundled_plugin()
    if path is None:
        pytest.skip("Build Prism first")
    outputs = []
    for bpm in (60, 120):
        plugin = IsolatedPlugin({"path": str(path), "parameters": {"21": 1, "22": 0.4}})
        try:
            chunks = []
            for block in range(48):
                midi = prism_tempo_events(plugin, bpm)
                assert midi, plugin.info.get("name")
                if block == 0:
                    midi += [([0x90, 60, 100], 0), ([0x90, 67, 100], 0)]
                chunks.append(plugin.render(None, 512, midi))
            outputs.append(np.concatenate(chunks))
        finally:
            plugin.close()
    assert np.max(np.abs(outputs[0] - outputs[1])) > 0.01


def test_prism_live_panic_keeps_processor_and_accepts_next_note():
    import time
    from mpclab.external_dsp import ExternalDSP
    from mpclab.plugin_host import IsolatedPlugin, LivePlugin
    from mpclab.prism import bundled_plugin

    path = bundled_plugin()
    if path is None:
        pytest.skip("Build Prism first")
    frames = 128
    plugin = IsolatedPlugin({"path": str(path)}, 48000)
    for block in range(3):
        plugin.render(None, frames, reset=block == 0)
    routing = ExternalDSP()
    routing.instrument = bridge = LivePlugin(plugin, frames)
    try:
        for _ in range(2):  # Loading panic, then panic after a held note.
            routing.panic()
            assert not routing.reset_requested
            routing.note_on(60, 0.8)
            peaks = []
            for block in range(32):
                output = np.zeros((frames, 2), np.float32)
                routing.render_instrument(output, frames, 48000)
                # Wait for this test's worker, without depending on CI's
                # ability to schedule a Python thread every 2.7 ms.
                deadline = time.monotonic() + 2
                while bridge.results.empty() and not bridge.error:
                    assert time.monotonic() < deadline, "Prism worker stalled"
                    time.sleep(0.001)
                assert not bridge.error, bridge.error
                peaks.append(float(np.max(np.abs(output))))
            assert max(peaks) > 0.01
    finally:
        routing.close()


def test_staging_updated_plugin_preserves_an_existing_loaded_mapping(tmp_path):
    import mmap
    from scripts.build_prism import stage_plugin

    source = tmp_path / "source"
    target = tmp_path / "installed"
    source.mkdir()
    target.mkdir()
    (source / "plugin.so").write_bytes(b"new binary contents")
    (target / "plugin.so").write_bytes(b"old binary contents")
    with (target / "plugin.so").open("rb") as file:
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            stage_plugin(source, target)
            assert mapped[:] == b"old binary contents"
            assert (target / "plugin.so").read_bytes() == b"new binary contents"


def test_performance_banks_are_portable_and_distinct():
    from collections import Counter
    from mpclab.ui.prism_controls import PERFORMANCES, SPECS

    specs = {s["id"]: s for s in SPECS}
    signatures = set()
    for item in PERFORMANCES:
        parse_sound(json.dumps(dict(item, format="anharmonic-prism", version=1)))
        for key, value in item["effects"].items():
            spec = specs[key]
            assert spec["low"] <= value <= spec["high"]
            if spec["step"]:
                assert value == int(value)
        patch = {k: v for k, v in item["patch"].items() if k != "name"}
        signature = json.dumps([patch, item["effects"], item.get("arp", {})], sort_keys=True)
        assert signature not in signatures
        signatures.add(signature)
    expansion = PERFORMANCES[24:]
    assert len(Counter(item["category"] for item in expansion)) == 8
    assert set(Counter(item["category"] for item in expansion).values()) == {12}
    for item in expansion:
        if item["category"] == "Motion - Arps":
            assert item["arp"]["enabled"]
        if item["category"] == "Cinema - Hits":
            assert item["patch"]["sustain"] == item["effects"]["b_sustain"] == 0
