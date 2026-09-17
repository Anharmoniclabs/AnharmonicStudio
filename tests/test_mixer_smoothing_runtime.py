import numpy as np

from mpclab.mixer_smoothing_runtime import RealtimeMixerControlSmoother


def test_first_manual_block_is_exact_and_uses_preallocated_buffers():
    smoother = RealtimeMixerControlSmoother(1000, 4, 2, time_ms=4)

    left, right = smoother.render(0, 0.8, 0.6, 4)

    assert left is smoother.left_buffers[0]
    assert right is smoother.right_buffers[0]
    np.testing.assert_array_equal(left, np.full(4, 0.8))
    np.testing.assert_array_equal(right, np.full(4, 0.6))


def test_manual_gain_pan_retarget_ramps_sample_by_sample_without_overshoot():
    smoother = RealtimeMixerControlSmoother(1000, 4, 1, time_ms=4)
    smoother.render(0, 1.0, 1.0, 4)

    left, right = smoother.render(0, 0.0, 0.5, 4)

    np.testing.assert_allclose(left, [0.75, 0.5, 0.25, 0.0])
    np.testing.assert_allclose(right, [0.875, 0.75, 0.625, 0.5])
    assert smoother._current_left[0] == 0.0
    assert smoother._current_right[0] == 0.5
    assert smoother._remaining_left[0] == 0
    assert smoother._remaining_right[0] == 0


def test_ramp_continues_exactly_across_callback_boundaries():
    smoother = RealtimeMixerControlSmoother(1000, 4, 1, time_ms=8)
    smoother.render(0, 1.0, 1.0, 4)

    first_left, _ = smoother.render(0, 0.0, 1.0, 4)
    first = first_left.copy()
    second_left, _ = smoother.render(0, 0.0, 1.0, 4)

    np.testing.assert_allclose(first, [0.875, 0.75, 0.625, 0.5])
    np.testing.assert_allclose(second_left, [0.375, 0.25, 0.125, 0.0])


def test_split_realtime_chunk_returns_exact_requested_view_length():
    smoother = RealtimeMixerControlSmoother(1000, 8, 1, time_ms=8)
    smoother.render(0, 1.0, 1.0, 8)

    left, right = smoother.render(0, 0.0, 0.5, 3)

    assert left.shape == (3,)
    assert right.shape == (3,)
    np.testing.assert_allclose(left, [0.875, 0.75, 0.625])
    np.testing.assert_allclose(right, [0.9375, 0.875, 0.8125])
    assert np.shares_memory(left, smoother.left_buffers[0])
    assert np.shares_memory(right, smoother.right_buffers[0])


def test_automation_endpoint_snap_prevents_return_to_stale_manual_value():
    smoother = RealtimeMixerControlSmoother(1000, 4, 1, time_ms=4)
    smoother.render(0, 1.0, 1.0, 4)
    smoother.snap(0, 0.25, 0.75)

    left, right = smoother.render(0, 0.25, 0.75, 4)

    np.testing.assert_array_equal(left, np.full(4, 0.25))
    np.testing.assert_array_equal(right, np.full(4, 0.75))


def test_oversized_emergency_callback_keeps_original_scalar_fast_path():
    smoother = RealtimeMixerControlSmoother(48000, 128, 1, time_ms=5)
    smoother.render(0, 1.0, 1.0, 128)

    left, right = smoother.render(0, 0.2, 0.4, 256)

    assert left == 0.2
    assert right == 0.4
    assert smoother._current_left[0] == 0.2
    assert smoother._current_right[0] == 0.4
