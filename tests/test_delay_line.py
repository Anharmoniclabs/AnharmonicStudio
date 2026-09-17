import numpy as np
import pytest

from mpclab.delay_line import DelayLine


def test_integer_delay_places_impulse_at_exact_frame():
    delay = DelayLine(8, 1)
    source = np.zeros((8, 1), dtype=np.float32)
    source[0, 0] = 1.0
    rendered = delay.process(source, 3)
    np.testing.assert_array_equal(rendered[:, 0], [0, 0, 0, 1, 0, 0, 0, 0])


def test_fractional_read_linearly_interpolates_history():
    delay = DelayLine(8, 1)
    delay.write([1.0])
    delay.write([3.0])
    assert delay.read(1.5)[0] == pytest.approx(2.0)


def test_feedback_produces_bounded_echo_train():
    delay = DelayLine(8, 1)
    source = np.zeros((10, 1), dtype=np.float32)
    source[0, 0] = 1.0
    rendered = delay.process(source, 2, feedback=0.5)
    assert rendered[2, 0] == pytest.approx(1.0)
    assert rendered[4, 0] == pytest.approx(0.5)
    assert rendered[6, 0] == pytest.approx(0.25)


def test_time_varying_delay_and_stereo_channels_remain_independent():
    delay = DelayLine(8, 2)
    source = np.zeros((6, 2), dtype=np.float32)
    source[0] = [1.0, 2.0]
    rendered = delay.process(source, np.array([1, 1, 2, 2, 3, 3], dtype=np.float64))
    assert np.isfinite(rendered).all()
    np.testing.assert_allclose(rendered[:, 1], rendered[:, 0] * 2)


def test_invalid_feedback_and_clear_are_safe():
    delay = DelayLine(4, 1)
    delay.write([1.0])
    delay.clear()
    assert delay.read(1)[0] == 0.0
    with pytest.raises(ValueError):
        delay.process(np.zeros((2, 1), dtype=np.float32), 1, feedback=1.0)
