import numpy as np
import pytest

from mpclab.dsp_test_signals import dc, impulse, noise, nyquist, sine, step


def test_reference_signals_have_expected_shapes_and_values():
    np.testing.assert_array_equal(impulse(4), [1, 0, 0, 0])
    np.testing.assert_array_equal(dc(3, 0.25), [0.25, 0.25, 0.25])
    np.testing.assert_array_equal(step(5, 2), [0, 0, 1, 1, 1])
    np.testing.assert_array_equal(nyquist(4), [1, -1, 1, -1])


def test_noise_is_seeded_and_sine_rejects_above_nyquist():
    np.testing.assert_array_equal(noise(16, seed=7), noise(16, seed=7))
    with pytest.raises(ValueError):
        sine(16, 600, 1000)
