from __future__ import annotations

import numpy as np

from mpclab.plugin_latency import PluginDelayCompensator


def test_delay_compensator_is_sample_exact_across_block_boundaries():
    pdc = PluginDelayCompensator(2, 3)
    pdc.configure(4)

    first = np.zeros((2, 3, 2), dtype=np.float32)
    first[0, :, 0] = [1.0, 2.0, 3.0]
    pdc.process(first, 3)
    np.testing.assert_array_equal(first[0, :, 0], [0.0, 0.0, 0.0])

    second = np.zeros((2, 3, 2), dtype=np.float32)
    second[0, :, 0] = [4.0, 5.0, 6.0]
    pdc.process(second, 3)
    np.testing.assert_array_equal(second[0, :, 0], [0.0, 1.0, 2.0])

    third = np.zeros((2, 3, 2), dtype=np.float32)
    third[0, :, 0] = [7.0, 8.0, 9.0]
    pdc.process(third, 3)
    np.testing.assert_array_equal(third[0, :, 0], [3.0, 4.0, 5.0])


def test_zero_delay_is_bit_transparent():
    pdc = PluginDelayCompensator(2, 8)
    block = np.arange(32, dtype=np.float32).reshape(2, 8, 2)
    before = block.copy()
    pdc.process(block, 8)
    np.testing.assert_array_equal(block, before)


def test_reconfigure_clears_old_history():
    pdc = PluginDelayCompensator(1, 4)
    pdc.configure(4)
    block = np.ones((1, 4, 2), dtype=np.float32)
    pdc.process(block, 4)
    pdc.configure(4)
    block.fill(2.0)
    pdc.process(block, 4)
    np.testing.assert_array_equal(block, 0.0)
