"""Repeated crossfades must blend the same slice head on every cycle."""

import numpy as np
import pytest

from mpclab.engine import PadRenderWorkspace
from test_audio_hardening import _voice


def loop_reference(data, start, stop, fade, rate, frames):
    """Scalar overlap-add reference, independent of the vector render scratch."""
    span = stop - start
    fade = min(fade, span // 2 - 1)

    def sample(position):
        left = int(position)
        fraction = position - left
        return (
            data[start + left % span] * (1 - fraction) + data[start + (left + 1) % span] * fraction
        )

    result = []
    for age in range(frames):
        travel = age * rate
        position = travel if travel < span else fade + (travel - span) % (span - fade)
        alpha = max(0.0, (position - (span - fade)) / fade)
        value = sample(position)
        if alpha > 0:
            value = value * (1 - alpha) + sample(position - (span - fade)) * alpha
        result.append(value * 0.5 * min(age, 1))
    return np.asarray(result, dtype=np.float32)


@pytest.mark.parametrize("rate", [0.75, 1.0, 2.0])
@pytest.mark.parametrize("start,fade", [(0, 10), (13, 10), (13, 80)])
@pytest.mark.parametrize("blocksize", [64, 257])
def test_repeated_crossfades_match_scalar_reference(rate, start, fade, blocksize):
    source = np.linspace(-0.8, 0.8, 150, dtype=np.float32)
    data = np.column_stack((source, -source))
    voice = _voice(data, loop=True, crossfade=fade, rate=rate, length=2000)
    voice.s0, voice.s1 = start, start + 100
    actual = np.zeros((1000, 2), dtype=np.float32)
    workspace = PadRenderWorkspace(blocksize)
    for at in range(0, len(actual), blocksize):
        voice.render(actual[at : at + blocksize], 0, workspace)
    expected = loop_reference(data, start, start + 100, fade, rate, len(actual))
    np.testing.assert_allclose(actual, expected, atol=1e-7, rtol=0)
