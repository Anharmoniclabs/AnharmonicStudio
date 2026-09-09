"""Headless tests for sampler range and zoom behavior."""

from __future__ import annotations

import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest

from mpclab.ui.waveform import WaveformView


class WaveformSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = WaveformView()
        self.view.resize(1000, 300)
        audio = np.zeros((441_000, 2), dtype=np.float32)
        peaks = np.zeros((100, 2), dtype=np.float32)
        self.view.set_clip(audio, peaks, 10.0, [0.0, 2.5, 5.0], 120.0)

    def test_new_clip_selects_entire_sample(self):
        self.assertEqual(self.view.selection(), (0.0, 10.0))

    def test_selection_is_ordered_and_clamped(self):
        self.view.set_selection(4.0, 2.0)
        self.assertEqual(self.view.selection(), (2.0, 4.0))
        self.view.set_selection(9.9, 12.0)
        self.assertEqual(self.view.selection(), (9.9, 10.0))

    def test_zoom_range_contains_selection_and_fit_resets(self):
        self.view.set_selection(1.25, 3.75)
        self.view.zoom_to_selection()
        self.assertLess(self.view.view_a, 0.125)
        self.assertGreater(self.view.view_b, 0.375)
        self.view.fit()
        self.assertEqual((self.view.view_a, self.view.view_b), (0.0, 1.0))

    def test_slice_bounds_can_be_used_as_pad_range(self):
        self.view.set_selection(*self.view.slice_bounds(1))
        self.assertEqual(self.view.selection(), (2.5, 5.0))

    def test_waveform_click_selects_once_without_competing_scrub(self):
        selected, scrubbed = [], []
        self.view.sliceSelected.connect(selected.append)
        self.view.scrubbed.connect(scrubbed.append)
        QTest.mouseClick(self.view, Qt.LeftButton, pos=QPoint(350, 120))
        self.assertEqual(selected, [1])
        self.assertEqual(scrubbed, [])
        self.assertEqual(self.view.selection(), (2.5, 5.0))
        QTest.mouseClick(self.view, Qt.LeftButton, pos=QPoint(350, 8))
        self.assertEqual(scrubbed, [3.5])

    def test_playhead_and_selection_reuse_traces_but_zoom_and_edits_invalidate(self):
        from unittest.mock import patch
        from mpclab.ui import waveform

        self.view.grab()
        with patch.object(waveform, "draw_peaks", wraps=waveform.draw_peaks) as draw:
            self.view.set_playhead(1.0)
            self.view.set_selection(2.0, 3.0)
            self.view.grab()
            self.assertEqual(draw.call_count, 0)
            self.view.zoom_by(0.5)
            self.view.grab()
            self.assertGreater(draw.call_count, 0)
        annotation_key = self.view._annotation_key
        self.view.markers[1] = 2.6
        self.view.grab()
        self.assertNotEqual(self.view._annotation_key, annotation_key)

    def test_zero_crossing_snap_moves_to_nearest_crossing(self):
        audio = np.ones((100, 2), dtype=np.float32)
        audio[50] = 0.10
        audio[51:] = -1.0
        audio[51] = -0.02
        peaks = np.zeros((10, 2), dtype=np.float32)
        self.view.set_clip(audio, peaks, 1.0, [])
        self.view.set_selection(0.50, 0.8, snap=True)
        self.assertAlmostEqual(self.view.selection_start, 0.51, places=2)

    def test_stereo_snap_does_not_treat_opposite_polarity_as_silence(self):
        audio = np.full((1000, 2), 0.8, dtype=np.float32)
        # The mono average changes sign here, but both channels are hot.
        audio[500] = (1.0, -0.9)
        audio[501] = (0.9, -1.0)
        # This later frame is a genuinely quiet cut in both channels.
        audio[506] = (0.01, -0.02)
        self.view.set_clip(audio, np.zeros((10, 2), dtype=np.float32), 1.0, [])

        self.assertAlmostEqual(self.view._snap_time(0.5), 0.506, places=6)

    def test_stereo_snap_uses_the_loudest_channel_for_cut_safety(self):
        audio = np.full((1000, 2), 0.7, dtype=np.float32)
        audio[500] = (0.01, 0.9)
        audio[501] = (0.0, 1.0)
        audio[502] = (0.08, -0.08)
        self.view.set_clip(audio, np.zeros((10, 2), dtype=np.float32), 1.0, [])

        self.assertAlmostEqual(self.view._snap_time(0.5), 0.502, places=6)

    def test_zero_crossing_snap_stays_within_twelve_milliseconds(self):
        audio = np.full((1000, 2), 0.4, dtype=np.float32)
        audio[513] = 0.0
        self.view.set_clip(audio, np.zeros((10, 2), dtype=np.float32), 1.0, [])

        self.assertAlmostEqual(self.view._snap_time(0.5), 0.5, places=6)

    def test_musical_snap_uses_detected_tempo(self):
        self.view.snap_mode = "1/16 grid"
        self.view.set_selection(1.31, 2.69, snap=True)
        self.assertEqual(self.view.selection(), (1.25, 2.75))


class DetectedSliceTests(unittest.TestCase):
    """A detected hit selects as the hit, not as the gap until the next one."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = WaveformView()
        self.view.resize(1000, 300)
        audio = np.zeros((441_000, 2), dtype=np.float32)
        peaks = np.zeros((100, 2), dtype=np.float32)
        self.view.set_clip(audio, peaks, 10.0, [0.0, 2.5, 5.0], 120.0, "clip-id")
        self.view.slice_kinds = {0: "kick", 1: "snare", 2: "hat"}
        self.view.slice_ends = {0: 0.18, 1: 2.62, 2: 5.09}

    def test_detected_end_bounds_the_slice(self):
        self.assertEqual(self.view.slice_bounds(0), (0.0, 0.18))
        self.assertEqual(self.view.slice_bounds(1), (2.5, 2.62))

    def test_detected_end_never_runs_past_the_next_marker(self):
        self.view.slice_ends[0] = 9.0
        self.assertEqual(self.view.slice_bounds(0), (0.0, 2.5))

    def test_slices_without_a_detected_end_run_to_the_next_marker(self):
        self.view.slice_ends = {}
        self.assertEqual(self.view.slice_bounds(1), (2.5, 5.0))

    def test_selecting_a_slice_takes_the_detected_range(self):
        self.view.select_slice(2, audition=False)
        start, end = self.view.selection()
        self.assertAlmostEqual(start, 5.0, places=4)
        self.assertAlmostEqual(end, 5.09, places=4)

    def test_new_clip_forgets_the_previous_detection(self):
        self.view.set_clip(
            np.zeros((1000, 2), dtype=np.float32), np.zeros((10, 2), dtype=np.float32), 1.0, []
        )
        self.assertEqual(self.view.slice_kinds, {})
        self.assertEqual(self.view.slice_ends, {})
        self.assertEqual(self.view.regions, [])


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = WaveformView()
        self.view.resize(1000, 300)
        audio = np.zeros((441_000, 2), dtype=np.float32)
        peaks = np.zeros((100, 2), dtype=np.float32)
        self.view.set_clip(audio, peaks, 10.0, [0.0, 2.5, 5.0], 120.0)

    def test_zoom_keeps_the_focus_point_and_stays_in_range(self):
        self.view.zoom_by(0.5, 0.5)
        self.assertLess(self.view.view_b - self.view.view_a, 1.0)
        self.assertGreaterEqual(self.view.view_a, 0.0)
        self.assertLessEqual(self.view.view_b, 1.0)
        self.assertLess(self.view.view_a, 0.5)
        self.assertGreater(self.view.view_b, 0.5)

    def test_panning_cannot_scroll_past_either_end(self):
        self.view.zoom_by(0.25, 0.5)
        span = self.view.view_b - self.view.view_a
        self.view.pan_by(-5.0)
        self.assertEqual(self.view.view_a, 0.0)
        self.view.pan_by(5.0)
        self.assertAlmostEqual(self.view.view_b, 1.0, places=6)
        self.assertAlmostEqual(self.view.view_b - self.view.view_a, span, places=6)

    def test_centre_on_puts_a_moment_in_the_middle(self):
        self.view.zoom_by(0.2, 0.0)
        self.view.centre_on(5.0)
        centre = (self.view.view_a + self.view.view_b) / 2 * self.view.duration
        self.assertAlmostEqual(centre, 5.0, delta=0.2)

    def test_time_and_pixel_conversion_round_trip(self):
        self.view.zoom_by(0.3, 0.4)
        for t in (0.0, 1.7, 4.2):
            if 0 <= self.view.time_to_x(t) <= self.view.width():
                self.assertAlmostEqual(self.view.x_to_time(self.view.time_to_x(t)), t, places=4)

    def test_ruler_and_range_band_split_the_widget(self):
        self.assertTrue(self.view._in_ruler(2.0))
        self.assertFalse(self.view._in_ruler(80.0))
        self.assertTrue(self.view._in_band(self.view.height() - 4))
        self.assertFalse(self.view._in_band(80.0))


if __name__ == "__main__":
    unittest.main()
