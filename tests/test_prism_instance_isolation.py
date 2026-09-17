"""Independent per-instrument external-plugin (Prism) ownership, without Qt.

Regression coverage for patterns/instruments no longer sharing one mutable
global Prism instance: each stable instrument id owns its own bridge, spec,
note routing and mixer track, both live and offline.
"""

import numpy as np

from mpclab.engine import Engine
from mpclab.library import Library
from mpclab.model import Clip, Project, SynthPatch
from mpclab.music import Note
from mpclab import engine_offline


class FakeBridge:
    """Deterministic stand-in for a loaded external plugin instance."""

    def __init__(self, name, level):
        self.info = {"name": name, "parameters": {}}
        self.level = level
        self.closed = False

    def render(self, _audio, frames, midi=(), **_kwargs):
        return np.full((frames, 2), self.level, np.float32)

    def close(self):
        self.closed = True


def engine_with_tracks(tmp_path, count=3):
    engine = Engine(Library(tmp_path / "audio"), sample_rate=8000, blocksize=128)
    while len(engine.project.tracks) < count:
        engine.project.add_track()
    engine.prepare_fx()
    return engine


def test_instrument_plugin_spec_round_trips_independently(tmp_path):
    project = Project()
    state_a = __import__("base64").b64encode(b"prism state a").decode()
    state_b = __import__("base64").b64encode(b"prism state b").decode()
    a = project.add_instrument(
        "Lead",
        SynthPatch(),
        plugin={"path": "/plugins/Anharmonic Prism.vst3", "parameters": {"0": 0.2}, "state": state_a},
    )
    b = project.add_instrument(
        "Pad",
        SynthPatch(),
        plugin={"path": "/plugins/Anharmonic Prism.vst3", "parameters": {"0": 0.9}, "state": state_b},
    )
    loaded = Project.from_dict(project.to_dict())
    loaded_a = next(i for i in loaded.instruments if i.id == a.id)
    loaded_b = next(i for i in loaded.instruments if i.id == b.id)
    assert loaded_a.plugin["parameters"] == {"0": 0.2}
    assert loaded_b.plugin["parameters"] == {"0": 0.9}
    assert loaded_a.plugin["state"] == state_a
    assert loaded_b.plugin["state"] == state_b
    # Editing one instrument's spec after load must never touch the other.
    loaded_a.plugin["parameters"]["0"] = 0.5
    assert loaded_b.plugin["parameters"] == {"0": 0.9}


def test_duplicate_instrument_clones_plugin_state_independently():
    project = Project()
    source = project.add_instrument(
        "Lead", SynthPatch(), plugin={"path": "/plugins/Anharmonic Prism.vst3", "parameters": {"0": 0.3}}
    )
    clone = project.add_instrument("Lead copy", source.patch, plugin=source.plugin)
    assert clone.plugin is not source.plugin
    clone.plugin["parameters"]["0"] = 0.9
    assert source.plugin["parameters"] == {"0": 0.3}


def test_engine_routes_notes_to_each_instruments_own_bridge_without_crosstalk(tmp_path):
    engine = engine_with_tracks(tmp_path)
    project = engine.project
    a = project.add_instrument("Lead", SynthPatch())
    b = project.add_instrument("Pad", SynthPatch())
    engine.external_for(a.id).instrument = FakeBridge("Anharmonic Prism", 0.25)
    engine.external_for(b.id).instrument = FakeBridge("Anharmonic Prism", -0.25)

    engine._spawn_synth(60, 1.0, gate_frames=64, instrument_id=a.id)
    engine._spawn_synth(60, 1.0, gate_frames=64, instrument_id=b.id)

    assert len(engine.external_for(a.id).voices) == 1
    assert len(engine.external_for(b.id).voices) == 1
    assert not engine.synth_voices  # both notes routed externally, not to the built-in synth
    assert engine.external.instrument is None  # the legacy/default slot is untouched

    engine._release_synth(60, instrument_id=a.id)
    assert engine.external_for(a.id).voices[0].dead
    assert not engine.external_for(b.id).voices[0].dead


def test_render_block_mixes_each_instrument_into_its_own_track(tmp_path):
    engine = engine_with_tracks(tmp_path)
    project = engine.project
    a = project.add_instrument("Lead", SynthPatch(track=1))
    b = project.add_instrument("Pad", SynthPatch(track=2))
    engine.external_for(a.id).instrument = FakeBridge("Anharmonic Prism", 0.25)
    engine.external_for(b.id).instrument = FakeBridge("Anharmonic Prism", -0.4)

    frames = 64
    outdata = np.zeros((frames, 2), np.float32)
    engine._callback(outdata, frames, None, False)

    assert np.allclose(engine._tbuf[1, :frames], 0.25)
    assert np.allclose(engine._tbuf[2, :frames], -0.4)
    # Neither instrument's signal leaks onto the other's or the synth's track.
    assert not np.any(engine._tbuf[2, :frames] == 0.25)
    assert not np.any(engine._tbuf[1, :frames] == -0.4)


def test_offline_bounce_partitions_synth_events_per_instrument(monkeypatch, tmp_path):
    engine = engine_with_tracks(tmp_path)
    project = engine.project
    a = project.add_instrument(
        "Lead", SynthPatch(track=1), plugin={"path": "/plugins/Anharmonic Prism.vst3"}
    )
    b = project.add_instrument(
        "Pad", SynthPatch(track=2), plugin={"path": "/plugins/Anharmonic Prism.vst3"}
    )
    pattern = project.pattern()
    pattern.bars = 1
    pattern.notes = [Note(60, 0, 1, 0.8, instrument=a.id), Note(64, 0, 1, 0.8, instrument=b.id)]
    project.rows[0].clips.append(
        Clip(kind="pattern", ref=pattern.id, start_beat=0, length_beats=4)
    )
    project.song_length_beats = 4

    captured = []

    class FakeOfflinePlugins:
        def __init__(self, specifications, sample_rate, synth_events, bpm=120):
            captured.append((specifications, list(synth_events)))
            self.instrument = FakeBridge("Anharmonic Prism", 0.0)
            self.effect = None
            self.events = []

        def render_instrument(self, destination, start, frames, parameters=None):
            pass

        def render_effect(self, block):
            pass

        def close(self):
            pass

    monkeypatch.setattr(engine_offline, "OfflinePlugins", FakeOfflinePlugins)
    list(engine.iter_offline_blocks(mode="song", tail=0))

    assert len(captured) == 2
    for _specs, events in captured:
        pitches = {event[1] for event in events}
        assert len(pitches) == 1  # each instrument's bridge only ever sees its own notes
