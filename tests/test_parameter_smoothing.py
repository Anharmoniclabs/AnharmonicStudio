import numpy as np
import pytest

import mpclab.parameter_smoothing as smoothing
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


def test_native_linear_fill_matches_reference_without_python_sample_loop(monkeypatch):
    native = smoothing.NATIVE
    if native is None:
        pytest.skip("Build native DSP with scripts/build_native.py")

    monkeypatch.setattr(smoothing, "NATIVE", None)
    reference = LinearSmoother(0.25, 48000, 7.5)
    expected = []
    for target, frames, time_ms in ((1.0, 127, None), (-0.5, 257, 3.0), (0.0, 513, 0.5)):
        reference.set_target(target, time_ms=time_ms)
        expected.append(reference.process(frames, dtype=np.float32))

    monkeypatch.setattr(smoothing, "NATIVE", native)
    accelerated = LinearSmoother(0.25, 48000, 7.5)

    def forbidden():
        raise AssertionError("native fill entered the Python per-sample loop")

    monkeypatch.setattr(accelerated, "next_value", forbidden)
    actual = []
    for target, frames, time_ms in ((1.0, 127, None), (-0.5, 257, 3.0), (0.0, 513, 0.5)):
        accelerated.set_target(target, time_ms=time_ms)
        actual.append(accelerated.process(frames, dtype=np.float32))

    np.testing.assert_allclose(np.concatenate(actual), np.concatenate(expected), rtol=0, atol=2e-7)
    assert accelerated.current == pytest.approx(reference.current, abs=1e-12)
    assert accelerated.remaining == reference.remaining


def test_native_onepole_fill_matches_reference_without_python_sample_loop(monkeypatch):
    native = smoothing.NATIVE
    if native is None:
        pytest.skip("Build native DSP with scripts/build_native.py")

    monkeypatch.setattr(smoothing, "NATIVE", None)
    reference = OnePoleSmoother(-0.25, 48000, 10.0)
    expected = []
    for target, frames, time_ms in ((1.0, 127, None), (-0.5, 257, 2.0), (0.75, 513, 20.0)):
        reference.set_target(target, time_ms=time_ms)
        expected.append(reference.process(frames, dtype=np.float32))

    monkeypatch.setattr(smoothing, "NATIVE", native)
    accelerated = OnePoleSmoother(-0.25, 48000, 10.0)

    def forbidden():
        raise AssertionError("native fill entered the Python per-sample loop")

    monkeypatch.setattr(accelerated, "next_value", forbidden)
    actual = []
    for target, frames, time_ms in ((1.0, 127, None), (-0.5, 257, 2.0), (0.75, 513, 20.0)):
        accelerated.set_target(target, time_ms=time_ms)
        actual.append(accelerated.process(frames, dtype=np.float32))

    np.testing.assert_allclose(np.concatenate(actual), np.concatenate(expected), rtol=0, atol=2e-7)
    assert accelerated.current == pytest.approx(reference.current, abs=1e-12)
