"""Master output protection and callback-boundary continuity."""

from __future__ import annotations

import unittest

import numpy as np

from mpclab.audio_kernel import MasteringKernel


class MasteringKernelTests(unittest.TestCase):
    def test_hot_signal_stays_below_codec_safe_ceiling(self):
        kernel = MasteringKernel(48_000, blocksize=64)
        audio = np.full((64, 2), 4.0, dtype=np.float32)
        kernel.process(audio)
        self.assertLessEqual(float(np.max(np.abs(audio))), kernel.ceiling + 1e-6)
        self.assertGreater(kernel.gain_reduction_db, 10.0)

    def test_stereo_link_preserves_channel_ratio(self):
        kernel = MasteringKernel(48_000)
        audio = np.array([[2.0, 0.5], [-2.0, -0.5]], dtype=np.float32)
        kernel.process(audio)
        np.testing.assert_allclose(audio[:, 1] / audio[:, 0], 0.25, atol=1e-6)

    def test_release_continues_across_callback_boundary(self):
        kernel = MasteringKernel(1_000, release_seconds=0.100)
        hot = np.full((4, 2), 2.0, dtype=np.float32)
        quiet = np.full((4, 2), 0.1, dtype=np.float32)
        kernel.process(hot)
        previous_gain = kernel.gain
        kernel.process(quiet)
        self.assertGreater(kernel.gain, previous_gain)
        self.assertLess(kernel.gain, 1.0)
        self.assertGreater(float(quiet[-1, 0]), float(quiet[0, 0]))

    def test_non_finite_input_cannot_escape_to_audio_device(self):
        kernel = MasteringKernel(48_000)
        audio = np.array([[np.nan, np.inf], [-np.inf, 0.25]], dtype=np.float32)
        kernel.process(audio)
        self.assertTrue(np.isfinite(audio).all())


if __name__ == "__main__":
    unittest.main()
