"""Buffer reuse must preserve tails across short blocks and loop seams."""

import numpy as np
import pytest

from mpclab.fx import BlockConvolver


@pytest.mark.parametrize("taps", [1, 17, 1536])
def test_variable_blocks_match_direct_convolution(taps):
    rng = np.random.default_rng(134)
    ir = (rng.normal(size=taps) * 0.01).astype(np.float32)
    sizes = [512, 1, 7, 256, 511, 3, 512]
    audio = rng.normal(size=(sum(sizes), 2)).astype(np.float32)
    expected = np.column_stack(
        [np.convolve(audio[:, channel], ir)[: len(audio)] for channel in range(2)]
    )
    convolver = BlockConvolver(ir)
    convolver.prepare(512)
    actual = audio.copy()
    start = 0
    for size in sizes:
        convolver.process(actual[start : start + size], prepared_only=True)
        start += size
    np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=4e-7)
    convolver.reset()
    silence = np.zeros((512, 2), dtype=np.float32)
    convolver.process(silence, prepared_only=True)
    assert not np.any(silence)
