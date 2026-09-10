"""Cross-platform audio equivalence and C ABI boundary regressions."""

from dataclasses import replace

import numpy as np
import pytest

from mpclab import audio_kernel, fx, sample_voice
from mpclab.native_dsp import NATIVE
from mpclab.sample_voice import PadVoice
from mpclab.model import DelayFX, ReverbFX

pytestmark = pytest.mark.skipif(NATIVE is None, reason="Build the C++ engine first")


@pytest.mark.parametrize("rate", [0.43, 1.0, 1.25, 3.17])
@pytest.mark.parametrize("loop,crossfade", [(False, 0), (True, 0), (True, 23)])
@pytest.mark.parametrize("quality", ["live", "offline"])
def test_sample_kernel_matches_reference_at_boundaries(monkeypatch, rate, loop, crossfade, quality):
    rng = np.random.default_rng(12)
    data = rng.normal(0, 0.1, (251, 2)).astype(np.float32)
    original = PadVoice(
        data,
        "source",
        11,
        239,
        rate,
        0.72,
        0.31,
        0.91,
        17,
        39,
        839,
        0,
        0,
        0,
        loop,
        loop_crossfade=crossfade,
        quality=quality,
    )
    renders, states = [], []
    for backend in (None, NATIVE):
        monkeypatch.setattr(sample_voice, "NATIVE", backend)
        voice = replace(original)
        blocks = []
        for index, size in enumerate((1, 17, 127, 256, 31, 513)):
            output = np.full((size + 3, 2), 0.031, dtype=np.float32)
            if index == 4:
                voice.release_now(81, delay=7)
            voice.render(output, 3)
            np.testing.assert_array_equal(output[:3], np.full((3, 2), 0.031, np.float32))
            blocks.append(output)
        renders.append(np.concatenate(blocks))
        states.append((voice.age, voice.dead))
    np.testing.assert_allclose(renders[0], renders[1], atol=1e-7, rtol=2e-6)
    assert states[0] == states[1]


def test_native_sample_rejects_range_before_writing():
    source = np.zeros((32, 2), np.float32)
    output = np.ones((16, 2), np.float32)
    voice = PadVoice(source, "x", -1, 33, 1, 1, 1, 1, 1, 1, 16, 0, 0, 0, False)
    with pytest.raises(ValueError, match="range"):
        NATIVE.core.sample(voice, output)
    np.testing.assert_array_equal(output, 1)
    with pytest.raises(ValueError, match="contiguous"):
        NATIVE.core.sample(replace(voice, s0=0, s1=32), output[::-1])


@pytest.mark.parametrize("frames", [1, 127, 256, 513, 4096])
def test_limiter_matches_reference_with_nonfinite_input_and_carried_gain(monkeypatch, frames):
    rng = np.random.default_rng(8)
    source = rng.normal(0, 1.2, (frames, 2)).astype(np.float32)
    source[0] = (np.nan, np.inf)
    results = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(audio_kernel, "NATIVE", backend)
        kernel = audio_kernel.MasteringKernel(48000)
        blocks = []
        for _ in range(4):
            blocks.append(kernel.process(source.copy()))
        results.append(np.concatenate(blocks))
    np.testing.assert_allclose(results[0], results[1], atol=1e-7, rtol=2e-6)
    assert np.isfinite(results[1]).all()


def test_compressor_matches_reference_through_knob_changes(monkeypatch):
    source = np.random.default_rng(2).normal(0, 0.7, (1009, 2)).astype(np.float32)
    results = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(fx, "NATIVE", backend)
        comp = fx.Compressor()
        blocks = []
        for threshold in (-18, -6, -24):
            for start in range(0, len(source), 127):
                blocks.append(
                    comp.process(source[start : start + 127].copy(), threshold, 3.1, 0.012, 0.18, 2)
                )
        results.append(np.concatenate(blocks))
    np.testing.assert_allclose(results[0], results[1], atol=2e-6, rtol=3e-5)


@pytest.mark.parametrize("drive", [0.0, 0.001, 0.2, 0.99])
def test_drive_matches_reference(monkeypatch, drive):
    source = np.linspace(-2, 2, 1024, dtype=np.float32).reshape(-1, 2)
    results = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(fx, "NATIVE", backend)
        results.append(fx.saturate(source.copy(), drive))
    np.testing.assert_allclose(results[0], results[1], atol=2e-7, rtol=2e-6)


def test_convolver_growth_preserves_existing_tail(monkeypatch):
    ir = np.random.default_rng(47).normal(0, 0.01, 1536).astype(np.float32)
    source = np.zeros((2048, 2), np.float32)
    source[0] = 1
    results = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(fx, "NATIVE", backend)
        convolver = fx.BlockConvolver(ir)
        output = source.copy()
        convolver.process(output[:17])
        convolver.process(output[17:512])
        convolver.process(output[512:])
        results.append(output)
    np.testing.assert_allclose(results[0], results[1], atol=1e-7, rtol=2e-6)


@pytest.mark.parametrize("kind", ["delay", "reverb"])
@pytest.mark.parametrize("frames", [127, 256, 512, 1024])
def test_sends_match_reference_including_tails_reset_and_parameter_changes(
    monkeypatch, kind, frames
):
    source = np.random.default_rng(81).normal(0, 0.07, (frames, 2)).astype(np.float32)
    renders = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(fx, "NATIVE", backend)
        send = fx.DelaySend() if kind == "delay" else fx.ReverbSend()
        settings = DelayFX() if kind == "delay" else ReverbFX()
        blocks = []
        for i in range(120):
            if i == 40:
                settings.damping = 0.9
                if kind == "delay":
                    settings.ping_pong = True
                else:
                    settings.width = 0.3
            block = source.copy() if i < 5 else np.zeros_like(source)
            output = (
                send.process(block, settings, 120)
                if kind == "delay"
                else send.process(block, settings)
            )
            blocks.append(output.copy())
        renders.append(np.concatenate(blocks))
        send.reset()
        silence = np.zeros_like(source)
        output = (
            send.process(silence, settings, 120)
            if kind == "delay"
            else send.process(silence, settings)
        )
        assert not np.any(output)
    np.testing.assert_allclose(renders[0], renders[1], atol=2e-6, rtol=3e-5)
