from __future__ import annotations

import numpy as np
import pytest

from mpclab.time_stretch import MODES, apply_fades, stretch_audio


def tone(frames=2048):
    t = np.arange(frames, dtype=np.float32) / 48_000
    wave = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    return np.column_stack((wave, wave))


@pytest.mark.parametrize("mode", MODES)
def test_every_stretch_mode_returns_exact_finite_length(mode):
    source = tone()
    rendered = stretch_audio(source, 3072, mode)
    assert rendered.shape == (3072, 2)
    assert rendered.dtype == np.float32
    assert np.isfinite(rendered).all()


def test_identity_length_is_stable_for_pitch_preserving_modes():
    source = tone(1024)
    for mode in ("beats", "percussion", "texture", "melodic", "complex"):
        rendered = stretch_audio(source, len(source), mode)
        assert rendered.shape == source.shape
        assert np.isfinite(rendered).all()


def test_equal_power_fades_reach_silence_at_the_outer_edges():
    source = np.ones((100, 2), dtype=np.float32)
    faded = apply_fades(source, 20, 30)
    assert faded[0, 0] == pytest.approx(0.0, abs=1e-7)
    assert faded[-1, 0] == pytest.approx(0.0, abs=1e-7)
    assert faded[40, 0] == pytest.approx(1.0)
    assert source[0, 0] == pytest.approx(1.0)  # source remains untouched


def test_invalid_stretch_mode_is_rejected():
    with pytest.raises(ValueError, match="unsupported stretch mode"):
        stretch_audio(tone(256), 512, "magic")
