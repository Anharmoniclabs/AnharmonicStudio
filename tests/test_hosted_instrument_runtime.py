from types import SimpleNamespace

import numpy as np

from mpclab.engine import Engine
from mpclab.external_dsp import ExternalDSP
from mpclab.model import SynthPatch
from mpclab.plugin_latency import HostedInstrumentDelayBank


class Plugin:
    def __init__(self, value=0.0, latency=0, blocksize=0):
        self.value = value
        self.blocksize = blocksize
        self.info = {
            "name": "Test",
            "instrument": True,
            "latency_samples": latency,
        }
        self.blocks = []
        self.closed = False

    def render(self, audio, frames, midi=(), **kwargs):
        self.blocks.append((list(midi), kwargs))
        return np.full((frames, 2), self.value, np.float32)

    def close(self):
        self.closed = True


class Library:
    pass


def test_owned_routes_keep_midi_and_lifecycle_isolated():
    external = ExternalDSP()
    first, second = Plugin(0.1), Plugin(0.2)
    external.set_instrument("first", first)
    external.set_instrument("second", second)
    external.note_on(60, 1, instrument_id="first", channel=2)
    external.note_on(67, 0.5, instrument_id="second", channel=3)

    one = np.zeros((8, 2), np.float32)
    two = np.zeros((8, 2), np.float32)
    external.render_instrument(one, 8, 1000, instrument_id="first")
    external.render_instrument(two, 8, 1000, instrument_id="second")

    assert first.blocks[0][0] == [([0x92, 60, 127], 0.0)]
    assert second.blocks[0][0] == [([0x93, 67, 64], 0.0)]
    np.testing.assert_array_equal(one, np.float32(0.1))
    np.testing.assert_array_equal(two, np.float32(0.2))

    removed = external.remove_instrument("first")
    removed.close()
    assert first.closed
    assert not second.closed
    assert external.instrument_for("second") is second


def test_engine_routes_stable_instrument_to_its_owned_host():
    engine = Engine(Library(), sample_rate=48000, blocksize=64)
    owned = engine.project.add_instrument("Hosted", SynthPatch(track=4))
    plugin = Plugin()
    engine.external.set_instrument(owned.id, plugin)

    engine._spawn_synth(61, 0.75, instrument_id=owned.id, midi_channel=5)
    assert not engine.synth_voices
    route = engine.external.instruments.route(owned.id)
    assert len(route.voices) == 1
    assert route.voices[0].channel == 5
    assert route.voices[0].note == 61

    engine._release_synth(61, instrument_id=owned.id, midi_channel=5)
    assert route.voices[0].dead


def test_hosted_delay_bank_aligns_faster_paths_to_slowest():
    fast = SimpleNamespace(info={"latency_samples": 4}, blocksize=0)
    slow = SimpleNamespace(info={"latency_samples": 7}, blocksize=0)
    bank = HostedInstrumentDelayBank(4)
    assert bank.configure({"fast": fast, "slow": slow}) == 7
    assert bank.path_latencies == {"fast": 4, "slow": 7}
    assert bank.delays["fast"].delay_samples == 3
    assert bank.delays["slow"].delay_samples == 0

    fast_block = np.zeros((4, 2), np.float32)
    fast_block[0] = 1
    bank.process("fast", fast_block, 4)
    np.testing.assert_array_equal(fast_block[:, 0], [0, 0, 0, 1])

    slow_block = np.zeros((4, 2), np.float32)
    slow_block[0] = 1
    bank.process("slow", slow_block, 4)
    np.testing.assert_array_equal(slow_block[:, 0], [1, 0, 0, 0])


def test_plugin_map_includes_legacy_and_owned_routes():
    external = ExternalDSP()
    legacy, owned = Plugin(), Plugin()
    external.instrument = legacy
    external.set_instrument("stable", owned)
    assert external.plugin_map() == {None: legacy, "stable": owned}
