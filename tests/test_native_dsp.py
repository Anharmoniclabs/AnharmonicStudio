"""Native acceleration must preserve patches, envelopes, and buffer boundaries."""

import numpy as np
import pytest

import mpclab.synth as synth
from mpclab.native_dsp import NATIVE
import mpclab.fx as fx

pytestmark = pytest.mark.skipif(
    NATIVE is None, reason="Build native DSP with scripts/build_native.py"
)


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
@pytest.mark.parametrize("patch_name", list(synth.PATCHES))
def test_native_patch_matches_reference_with_odd_blocks_and_release(monkeypatch, rate, patch_name):
    patch = synth.patch_copy(patch_name)
    chunks = (127, 256, 513, 1, 255, 512, 128, 1024, 2048)
    outputs = []
    states = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(synth, "NATIVE", backend)
        voice = synth.SynthVoice(61, 0.83, rate, start_offset=17, gate_frames=1049)
        blocks = []
        for size in chunks:
            out = np.zeros((size, 2), dtype=np.float32)
            voice.render(out, patch)
            blocks.append(out)
        outputs.append(np.concatenate(blocks))
        states.append((voice.stage, voice.dead, voice.age, voice.envelope))
    np.testing.assert_allclose(outputs[1], outputs[0], rtol=3e-5, atol=2e-6)
    assert states[0][:3] == states[1][:3]
    assert states[0][3] == pytest.approx(states[1][3], abs=1e-12)


def test_native_rejects_mismatched_buffers():
    source = np.zeros(8)
    with pytest.raises(ValueError):
        NATIVE.filter(source, np.zeros(2), source, np.zeros(4), source, source, 1)
    with pytest.raises(ValueError):
        NATIVE.synth(np.zeros((8, 2), dtype=np.float64), source, np.zeros(23), np.zeros(12), 0, -1)


def test_native_gate_and_manual_note_off_match_reference(monkeypatch):
    output = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(synth, "NATIVE", backend)
        voice = synth.SynthVoice(72, 1, 48000)
        patch = synth.patch_copy("Copper Pluck")
        out = np.zeros((8192, 2), dtype=np.float32)
        voice.render(out[:256], patch)
        voice.note_off(0.008)
        voice.render(out[256:], patch)
        output.append(out)
        assert voice.dead
    np.testing.assert_allclose(output[0], output[1], rtol=3e-5, atol=2e-6)


@pytest.mark.parametrize("channels", [1, 2])
def test_native_smoothing_matches_fft_reference_through_parameter_changes(monkeypatch, channels):
    rng = np.random.default_rng(3)
    source = rng.normal(size=(4096, channels)).astype(np.float32)
    outputs = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(fx, "NATIVE", backend)
        smoother = fx.OnePole(channels)
        blocks = []
        for coefficient in (0.1, 0.95, 0.999999, 0.99, 0):
            for offset in range(0, len(source), 127):
                blocks.append(smoother.process(source[offset : offset + 127].copy(), coefficient))
        outputs.append(np.concatenate(blocks))
    np.testing.assert_allclose(outputs[0], outputs[1], atol=2e-6, rtol=3e-5)
