"""Callback timing instrumentation.

Deciding a block size needs the worst callback, not the average one, so the
engine keeps a rolling window and the GUI takes percentiles off it. These
tests pin the two properties that make the window trustworthy: it must not
allocate while the audio thread writes it, and it must report the tail
honestly rather than smoothing it away.
"""

from __future__ import annotations

import unittest

import numpy as np

from mpclab.engine import CALLBACK_HISTORY, MAX_PAD_VOICES, Engine


class _SilentLibrary:
    def audio(self, sample_id):
        return np.zeros((480, 2), dtype=np.float32)

    def duration(self, sample_id):
        return 0.01

    def get(self, sample_id):
        return None


def _run(engine: Engine, blocks: int) -> None:
    out = np.zeros((engine.blocksize, 2), dtype=np.float32)
    for _ in range(blocks):
        engine._callback(out, engine.blocksize, None, None)


class CallbackTimingTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(_SilentLibrary(), sample_rate=48_000, blocksize=256)

    def test_empty_window_reports_a_usable_period(self):
        stats = self.engine.timing_stats()
        self.assertEqual(stats["blocks"], 0)
        self.assertAlmostEqual(stats["period"], 256 / 48_000 * 1000.0, places=6)

    def test_window_fills_and_then_wraps_without_growing(self):
        _run(self.engine, 20)
        self.assertEqual(self.engine.timing_stats()["blocks"], 20)

        _run(self.engine, CALLBACK_HISTORY * 2)
        stats = self.engine.timing_stats()
        self.assertEqual(stats["blocks"], CALLBACK_HISTORY)
        self.assertEqual(len(self.engine._cb_times), CALLBACK_HISTORY)

    def test_percentiles_bracket_the_worst_callback(self):
        _run(self.engine, 64)
        stats = self.engine.timing_stats()
        self.assertLessEqual(stats["p50"], stats["p99"])
        self.assertLessEqual(stats["p99"], stats["max"])
        self.assertGreater(stats["max"], 0.0)

    def test_headroom_is_the_share_of_the_period_left_over(self):
        _run(self.engine, 64)
        stats = self.engine.timing_stats()
        expected = max(0.0, 1.0 - stats["max"] / stats["period"])
        self.assertAlmostEqual(stats["headroom"], expected, places=9)
        self.assertGreaterEqual(stats["headroom"], 0.0)

    def test_a_callback_that_overruns_shows_zero_headroom(self):
        self.engine._cb_times[0] = self.engine.period_ms * 3.0 / 1000.0
        self.engine._cb_filled = 1
        self.assertEqual(self.engine.timing_stats()["headroom"], 0.0)

    def test_reset_clears_the_window_and_the_xrun_count(self):
        _run(self.engine, 32)
        self.engine.underruns = 7
        self.engine.reset_timing()
        stats = self.engine.timing_stats()
        self.assertEqual(stats["blocks"], 0)
        self.assertEqual(stats["xruns"], 0)
        self.assertEqual(float(self.engine._cb_times.max()), 0.0)

    def test_timing_window_is_preallocated_once(self):
        before = self.engine._cb_times
        _run(self.engine, CALLBACK_HISTORY + 50)
        self.assertIs(self.engine._cb_times, before)

    def test_live_pad_polyphony_is_bounded(self):
        self.engine.project.pads[0].sample_id = "sample"
        self.engine.project.pads[0].end = 0.01
        for _ in range(MAX_PAD_VOICES * 3):
            self.engine.trigger_pad(0)
        _run(self.engine, 1)
        live = [voice for voice in self.engine.voices if voice.pad_index >= 0]
        self.assertLessEqual(len(live), MAX_PAD_VOICES)


if __name__ == "__main__":
    unittest.main()
