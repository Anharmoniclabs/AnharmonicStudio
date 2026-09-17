import numpy as np
import pytest

from mpclab.parameter_smoothing import LinearSmoother, OnePoleSmoother, smoothing_frames


def test_linear_smoother_reaches_exact_target_without_overshoot():
    smoother = LinearSmoother(0.0, 1000, 10)
    smoother.set_target(1.0)
    values = smoother.process(12, dtype=np.float64)
    np.testing.assert_allclose(values[:10], np.linspace(0.1, 1.0, 10))
    np.testing.assert_array_equal(values[10:], [1.0, 1.0])
    assert smoother.current == 1.0
    assert smoother.remaining == 0


def test_linear_smoother_retargets_from_current_value():
    smoother = LinearSmoother(0.0, 1000, 10)
    smoother.set_target(1.0)
    smoother.process(5)
    assert smoother.current == pytest.approx(0.5)
    smoother.set_target(-0.5, time_ms=5)
    values = smoother.process(5, dtype=np.float64)
    np.testing.assert_allclose(values, [0.3, 0.1, -0.1, -0.3, -0.5])


def test_one_pole_is_monotonic_and_zero_time_snaps():
    smoother = OnePoleSmoother(0.0, 1000, 10)
    smoother.set_target(1.0)
    values = smoother.process(40, dtype=np.float64)
    assert np.all(np.diff(values) > 0)
    assert 0.95 < values[-1] < 1.0
    smoother.set_target(-2.0, time_ms=0)
    assert smoother.current == -2.0


def test_smoothing_frames_validates_clock():
    assert smoothing_frames(48000, 10) == 480
    with pytest.raises(ValueError):
        smoothing_frames(0, 10)
