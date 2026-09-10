"""Buffer reuse must preserve tails across short blocks and loop seams."""

import numpy as np
import pytest

import mpclab.fx as fx
from mpclab.fx import BlockConvolver
from mpclab.native_dsp import NATIVE


@pytest.mark.parametrize("taps", [1, 17, 1536])
@pytest.mark.parametrize("channels", [1, 2])
def test_variable_blocks_match_direct_convolution(taps, channels):
    rng = np.random.default_rng(134)
    ir = (rng.normal(size=taps) * 0.01).astype(np.float32)
    sizes = [512, 1, 7, 256, 511, 3, 512]
    audio = rng.normal(size=(sum(sizes), channels)).astype(np.float32)
    expected = np.column_stack(
        [np.convolve(audio[:, channel], ir)[: len(audio)] for channel in range(channels)]
    )
    convolver = BlockConvolver(ir, channels=channels)
    convolver.prepare(512)
    actual = audio.copy()
    start = 0
    for size in sizes:
        convolver.process(actual[start : start + size], prepared_only=True)
        start += size
    np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=4e-7)
    convolver.reset()
    silence = np.zeros((512, channels), dtype=np.float32)
    convolver.process(silence, prepared_only=True)
    assert not np.any(silence)


@pytest.mark.parametrize("ir_scale", [1e-30, 1e30])
@pytest.mark.parametrize("channels", [1, 2])
def test_finite_extreme_convolution_keeps_channel_output_and_short_block_tails(ir_scale, channels):
    rng = np.random.default_rng(219)
    ir = (rng.uniform(-1, 1, 17) * ir_scale).astype(np.float32)
    audio = (rng.uniform(-1, 1, (137, channels)) / ir_scale).astype(np.float32)
    expected = np.column_stack(
        [np.convolve(audio[:, c].astype(np.float64), ir)[: len(audio)] for c in range(channels)]
    )
    convolver = BlockConvolver(ir, channels=channels)
    convolver.prepare(128)
    actual = audio.copy()
    for start, end in ((0, 128), (128, 129), (129, 137)):
        convolver.process(actual[start:end], prepared_only=True)
    np.testing.assert_allclose(actual, expected, rtol=3e-6, atol=5e-7)


@pytest.mark.skipif(NATIVE is None, reason="Build the C++ engine first")
@pytest.mark.parametrize("invalid", [np.nan, np.inf])
@pytest.mark.parametrize("invalid_ir", [False, True])
@pytest.mark.parametrize("channels", [1, 2])
def test_nonfinite_convolution_preserves_reference_contamination_and_reset(
    monkeypatch, invalid, invalid_ir, channels
):
    ir = np.full(17, 0.01, np.float32)
    source = np.ones((128, channels), np.float32)
    if invalid_ir:
        ir[3] = invalid
    else:
        source[3] = invalid
    results = []
    for backend in (None, NATIVE):
        monkeypatch.setattr(fx, "NATIVE", backend)
        with np.errstate(invalid="ignore", over="ignore"):
            convolver = BlockConvolver(ir, channels=channels)
            convolver.prepare(128)
            output = source.copy()
            convolver.process(output, prepared_only=True)
            convolver.reset()
            clean = np.ones((128, channels), np.float32)
            convolver.process(clean, prepared_only=True)
        results.append((output, clean))
    for reference, native in zip(results[0], results[1], strict=True):
        np.testing.assert_allclose(reference, native, rtol=1e-5, atol=4e-7, equal_nan=True)
