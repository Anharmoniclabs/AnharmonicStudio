"""Tests for mapping a freeform waveform range onto an MPC pad."""

from __future__ import annotations

import unittest

from mpclab.model import Pad, map_sample_range


class SamplerMappingTests(unittest.TestCase):
    def test_range_maps_to_selected_pad(self):
        pad = map_sample_range(Pad(), "song", 2.25, 4.75, "My Song cut")
        self.assertEqual(pad.sample_id, "song")
        self.assertEqual((pad.start, pad.end), (2.25, 4.75))
        self.assertEqual(pad.name, "My Song cut")

    def test_retrimming_same_pad_preserves_performance_settings(self):
        pad = Pad(sample_id="song", mode="loop", reverse=True)
        map_sample_range(pad, "song", 2.25, 4.75, "My Song cut")
        self.assertEqual(pad.mode, "loop")
        self.assertTrue(pad.reverse)

    def test_replacing_sample_resets_direction_and_mode(self):
        pad = Pad(sample_id="old", mode="loop", reverse=True)
        map_sample_range(pad, "song", 2.25, 4.75, "My Song cut")
        self.assertEqual(pad.mode, "one-shot")
        self.assertFalse(pad.reverse)

    def test_empty_ranges_are_rejected(self):
        with self.assertRaises(ValueError):
            map_sample_range(Pad(), "song", 2.0, 2.0)


if __name__ == "__main__":
    unittest.main()
