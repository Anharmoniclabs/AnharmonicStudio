"""Audio routing plus opt-in checks against locally installed, trusted VST3s."""

import base64
import os

import numpy as np
import pytest

from mpclab.engine import Engine
from mpclab.external_dsp import ExternalDSP, OfflinePlugins
from mpclab.library import Library
from mpclab.model import Project
from mpclab.music import Note
from mpclab.plugin_host import IsolatedPlugin, PluginError, UnavailablePlugin


class CapturePlugin:
    def __init__(self):
        self.blocks = []

    def render(self, audio, frames, midi=(), **kwargs):
        self.blocks.append((frames, midi, kwargs))
        return np.full((frames, 2), 0.125, np.float32)

    def close(self):
        pass


def test_live_gate_crosses_block_boundary_and_panic_clears_future_notes():
    routing = ExternalDSP()
    routing.instrument = plugin = CapturePlugin()
    routing.note_on(60, 1, offset=3, gate=20, live=False)
    block = np.zeros((16, 2), np.float32)
    routing.render_instrument(block, 16, 1000)
    assert plugin.blocks[0][1] == [([0x91, 60, 127], 0.003)]
    routing.render_instrument(block, 16, 1000)
    assert plugin.blocks[1][1] == [([0x81, 60, 0], 0.007)]
    routing.note_on(70, 0.5, gate=100)
    routing.panic()
    routing.render_instrument(block, 16, 1000)
    assert not routing.ends
    assert plugin.blocks[-1][2]["reset"]
    assert all(message[1] == 123 for message, at in plugin.blocks[-1][1])


def test_missing_instrument_stays_silent_and_effect_failure_preserves_dry_audio(tmp_path):
    engine = Engine(Library(tmp_path / "library"), blocksize=128)
    engine.external.instrument = UnavailablePlugin("Missing")
    engine.synth_note_on(60)
    output = np.zeros((128, 2), np.float32)
    engine._callback(output, 128, None, False)
    assert not output.any()
    engine.external.effect = UnavailablePlugin("Failed")
    output[:] = 0.2
    engine.external.render_effect(output)
    assert np.all(output == np.float32(0.2))


def test_offline_events_keep_exact_sample_offsets_and_release_at_boundaries(monkeypatch):
    class Plugin(CapturePlugin):
        def __init__(self, *args):
            super().__init__()
            self.info = {"instrument": True}

    monkeypatch.setattr("mpclab.plugin_host.IsolatedPlugin", Plugin)
    routing = OfflinePlugins({"instrument": {}}, 1000, [(7, 60, 1, 9), (16, 60, 0.5, 4)])
    try:
        destination = np.zeros((16, 2), np.float32)
        routing.render_instrument(destination, 0, 16)
        routing.render_instrument(destination, 16, 16)
        assert routing.instrument.blocks[0][1] == [([0x91, 60, 127], 0.007)]
        assert routing.instrument.blocks[1][1] == [
            ([0x81, 60, 0], 0),
            ([0x91, 60, 64], 0),
            ([0x81, 60, 0], 0.004),
        ]
    finally:
        routing.close()


@pytest.mark.parametrize("slot", ["instrument", "effect"])
def test_real_plugin_audio_state_and_export(slot, tmp_path):
    path = os.environ.get("ANHARMONIC_TEST_" + slot.upper())
    if not path:
        pytest.skip("Set ANHARMONIC_TEST_" + slot.upper() + " to a trusted native VST3")
    plugin = IsolatedPlugin({"path": path})
    try:
        assert plugin.info[slot]
        assert plugin.info["parameters"]
        info = plugin.info
        source = np.column_stack([np.sin(np.arange(512) * 0.04)] * 2).astype(np.float32) * 0.2
        output = plugin.render(
            None if slot == "instrument" else source, 512, [([0x90, 60, 100], 0)]
        )
        assert output.shape == (512, 2) and np.isfinite(output).all()
        assert np.max(np.abs(output)) > 0.0001
        parameters = {key: p["value"] for key, p in info["parameters"].items()}
        adjustable = next(
            (
                key
                for key, p in info["parameters"].items()
                if p["name"].lower() in {"cutoff", "mix"}
            ),
            next(iter(parameters)),
        )
        parameters[adjustable] = 0.37
    finally:
        plugin.close()
    spec = {"path": path, "state": info["state"], "parameters": parameters}
    restored = IsolatedPlugin(spec)
    try:
        assert restored.info["parameters"][adjustable]["value"] == pytest.approx(0.37, abs=0.02)
        assert base64.b64decode(restored.info["state"])
    finally:
        restored.close()
    project = Project()
    project.plugins = {slot: spec}
    project.pattern().notes = [Note(60, 0.125, 0.5, 0.8)]
    saved = tmp_path / "plugin.json"
    project.save(saved)
    engine = Engine(Library(tmp_path / "library"), blocksize=512)
    engine.project = Project.load(saved)
    rendered = np.concatenate(list(engine.iter_offline_blocks("pattern", tail=0.013)))
    assert np.isfinite(rendered).all() and np.max(np.abs(rendered)) > 0.0001


def test_missing_plugin_export_reports_failure(tmp_path):
    engine = Engine(Library(tmp_path / "library"))
    engine.project.plugins = {"effect": {"path": str(tmp_path / "Missing.vst3")}}
    with pytest.raises(PluginError):
        list(engine.iter_offline_blocks("pattern", tail=0))
