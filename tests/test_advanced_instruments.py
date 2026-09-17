from __future__ import annotations

import numpy as np

from mpclab.expansion_instruments import ExpansionInstrumentBridge, MultisampleFactory


class SampleLibrary:
    def __init__(self):
        self.samples = {
            "soft-a": np.full((512, 2), 0.10, dtype=np.float32),
            "soft-b": np.full((512, 2), 0.20, dtype=np.float32),
            "hard": np.full((512, 2), 0.40, dtype=np.float32),
            "release": np.full((128, 2), 0.05, dtype=np.float32),
        }

    def cached_audio(self, sample_id):
        return self.samples.get(sample_id)


def render_note(bridge, note=60, velocity=100, frames=1024):
    output = bridge.render(
        None,
        frames,
        [([0x90, note, velocity], 0.0), ([0x80, note, 0], frames / bridge.sample_rate * 0.5)],
    )
    assert output.shape == (frames, 2)
    assert np.isfinite(output).all()
    assert float(np.max(np.abs(output))) > 1e-6
    return output


def test_multisample_key_velocity_round_robin_stack_and_release_samples():
    library = SampleLibrary()
    state = {
        "regions": [
            {
                "sample_id": "soft-a",
                "key_low": 48,
                "key_high": 72,
                "root_key": 60,
                "velocity_low": 1,
                "velocity_high": 80,
                "round_robin": 0,
                "stack": 0,
                "gain": 1.0,
                "release_sample_id": "release",
            },
            {
                "sample_id": "soft-b",
                "key_low": 48,
                "key_high": 72,
                "root_key": 60,
                "velocity_low": 1,
                "velocity_high": 80,
                "round_robin": 1,
                "stack": 0,
                "gain": 1.0,
                "release_sample_id": "release",
            },
            {
                "sample_id": "hard",
                "key_low": 48,
                "key_high": 72,
                "root_key": 60,
                "velocity_low": 81,
                "velocity_high": 127,
                "round_robin": 0,
                "stack": 0,
                "gain": 1.0,
                "release_sample_id": None,
            },
        ],
        "polyphony": 32,
    }
    factory = MultisampleFactory(library, state)
    first = factory.note_on(60, 0.5)
    second = factory.note_on(60, 0.5)
    hard = factory.note_on(60, 1.0)
    assert first[0].audio is library.samples["soft-a"]
    assert second[0].audio is library.samples["soft-b"]
    assert hard[0].audio is library.samples["hard"]
    releases = factory.release_voices(first)
    assert len(releases) == 1
    assert releases[0].audio is library.samples["release"]


def test_prism_vector_four_sources_xy_gesture_and_noise_are_deterministic():
    state = {
        "sources": ["sine", "saw", "triangle", "noise"],
        "x": 0.5,
        "y": 0.5,
        "gesture": [
            {"at": 0.0, "x": 0.0, "y": 0.0},
            {"at": 0.02, "x": 1.0, "y": 1.0},
        ],
        "gesture_loop": True,
    }
    first = ExpansionInstrumentBridge("vector", state, 48_000)
    second = ExpansionInstrumentBridge("vector", state, 48_000)
    a = render_note(first, frames=2048)
    b = render_note(second, frames=2048)
    np.testing.assert_array_equal(a, b)
    assert not np.allclose(a[:512], a[1024:1536])


def test_modal_string_and_fm4_engines_render_and_release_without_nonfinite_audio():
    fm_operators = [
        {
            "ratio": ratio,
            "fixed_hz": 0.0,
            "level": level,
            "attack": 0.002,
            "decay": 0.05,
            "sustain": 0.7,
            "release": 0.08,
        }
        for ratio, level in ((1.0, 1.0), (2.0, 0.7), (3.0, 0.4), (4.0, 0.25))
    ]
    configs = {
        "modal": {
            "modes": [
                {"ratio": 1.0, "gain": 1.0, "decay": 0.4},
                {"ratio": 2.01, "gain": 0.5, "decay": 0.25},
                {"ratio": 3.9, "gain": 0.25, "decay": 0.15},
            ]
        },
        "string": {"damping": 0.992, "brightness": 0.65, "pick_position": 0.22},
        "fm4": {"operators": fm_operators, "algorithm": 0, "feedback": 0.25},
    }
    for engine_type, state in configs.items():
        bridge = ExpansionInstrumentBridge(engine_type, state, 48_000)
        output = render_note(bridge, frames=4096)
        assert float(np.sqrt(np.mean(output * output))) > 1e-5
        tail = bridge.render(None, 4096, [])
        assert np.isfinite(tail).all()


def test_multisample_bridge_renders_regions_through_the_common_instrument_contract():
    library = SampleLibrary()
    state = {
        "regions": [
            {
                "sample_id": "soft-a",
                "key_low": 0,
                "key_high": 127,
                "root_key": 60,
                "velocity_low": 1,
                "velocity_high": 127,
                "round_robin": 0,
                "stack": 0,
                "gain": 1.0,
                "release_sample_id": "release",
            }
        ],
        "polyphony": 16,
    }
    bridge = ExpansionInstrumentBridge("multisample", state, 48_000, library)
    output = bridge.render(
        None,
        512,
        [([0x90, 60, 100], 0.0), ([0x80, 60, 0], 256 / 48_000)],
    )
    assert np.isfinite(output).all()
    assert np.max(np.abs(output[:256])) > 0.01
    assert np.max(np.abs(output[256:])) > 0.001
