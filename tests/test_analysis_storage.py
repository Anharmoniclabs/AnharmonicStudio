"""Long-song analysis matches the spectral reference without whole-song FFT scratch."""

import numpy as np
import pytest

from mpclab.dsp import HOP, WIN, onset_envelope


@pytest.mark.parametrize("length", [0, 37, WIN, HOP * 520 + 17])
def test_batched_onsets_match_reference_across_boundaries(length, monkeypatch):
    wave = np.random.default_rng(123).normal(0, 0.1, length).astype(np.float32)
    padded = np.pad(wave, (0, max(0, WIN - length)))
    windows = np.lib.stride_tricks.sliding_window_view(padded, WIN)[::HOP]
    magnitude = np.abs(np.fft.rfft(windows * np.hanning(WIN).astype(np.float32), axis=1)).astype(
        np.float32
    )
    log = np.log1p(magnitude * 8.0)
    reference = np.maximum(np.diff(log, axis=0, prepend=log[:1]), 0).sum(axis=1)
    if reference.max() > 0:
        reference /= reference.max()
    transform = np.fft.rfft

    def bounded_transform(frames, *args, **kwargs):
        assert len(frames) <= 256, "analysis allocated whole-song FFT input"
        return transform(frames, *args, **kwargs)

    monkeypatch.setattr(np.fft, "rfft", bounded_transform)
    np.testing.assert_allclose(onset_envelope(wave), reference, rtol=2e-6, atol=1e-7)
