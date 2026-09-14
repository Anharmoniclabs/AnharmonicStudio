"""The CHOP editor's preview voice: position feedback and looping."""

from __future__ import annotations

import sys
import types
import unittest

import numpy as np

sys.modules.setdefault("sounddevice", types.SimpleNamespace(OutputStream=object))

from mpclab.engine import Engine, AUDITION

SR = 8000


class _Library:
    """One second of full-scale noise, so any played span is audible."""

    def __init__(self):
        rng = np.random.default_rng(3)
        self.data = rng.standard_normal((SR, 2)).astype(np.float32) * 0.3

    def audio(self, _sample_id):
        return self.data

    def reversed_audio(self, _sample_id):
        return self.data[::-1]


class AuditionTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(_Library(), sample_rate=SR, blocksize=128)

    def run_blocks(self, count: int = 1) -> np.ndarray:
        out = np.zeros((128, 2), dtype=np.float32)
        for _ in range(count):
            out[:] = 0.0
            self.engine._callback(out, 128, None, False)
        return out

    def test_position_tracks_the_preview_and_clears_after_it(self):
        self.engine.audition("clip", 0.25, 0.75)
        self.run_blocks()
        first = self.engine.audition_time
        self.assertIsNotNone(first)
        self.assertGreaterEqual(first, 0.25)
        self.run_blocks()
        self.assertGreater(self.engine.audition_time, first)
        # 0.5 s of audio at 128 frames a block is over well inside 40 blocks.
        self.run_blocks(40)
        self.assertIsNone(self.engine.audition_time)

    def test_looped_preview_wraps_and_keeps_playing(self):
        self.engine.audition("clip", 0.1, 0.2, loop=True)
        self.run_blocks(30)  # far past one pass of the range
        position = self.engine.audition_time
        self.assertIsNotNone(position)
        self.assertGreaterEqual(position, 0.1)
        self.assertLessEqual(position, 0.2 + 1e-6)

    def test_a_second_preview_replaces_the_first(self):
        self.engine.audition("clip", 0.0, 0.9)
        self.run_blocks()
        self.engine.audition("clip", 0.5, 0.9)
        self.run_blocks(3)
        live = [v for v in self.engine.voices if v.pad_index == AUDITION and not v.dead]
        self.assertLessEqual(len(live), 1)

    def test_stop_audition_silences_the_preview(self):
        self.engine.audition("clip", 0.0, 0.9)
        self.run_blocks()
        self.engine.stop_audition()
        self.run_blocks(6)
        self.assertIsNone(self.engine.audition_time)

    def test_preview_survives_a_transport_stop(self):
        # Stopping the transport releases pad voices; the editor preview is
        # not one of them and must keep playing.
        self.engine.audition("clip", 0.0, 0.9)
        self.run_blocks()
        self.engine.stop_transport(rewind=True)
        self.run_blocks(2)
        self.assertIsNotNone(self.engine.audition_time)

    def test_scrub_burst_starts_only_the_latest_position_in_the_next_block(self):
        for index in range(1000):
            self.engine.audition("clip", index / 2000, 0.9)
        out = self.run_blocks()
        voices = [voice for voice in self.engine.voices if voice.pad_index == AUDITION]
        self.assertEqual(len(voices), 1)
        self.assertEqual(voices[0].s0, int(0.4995 * SR))
        self.assertTrue(np.any(out))

    def test_stop_and_panic_cancel_pending_scrubs_in_command_order(self):
        for stop in (self.engine.stop_audition, self.engine.panic):
            self.engine.audition("clip", 0.1, 0.9)
            stop()
            self.assertFalse(np.any(self.run_blocks()))
            stop()
            self.engine.audition("clip", 0.2, 0.9)
            self.assertTrue(np.any(self.run_blocks()))
            self.engine.stop_audition()
            self.run_blocks(6)


if __name__ == "__main__":
    unittest.main()
