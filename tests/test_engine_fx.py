"""Effects have to reach the bounce, not just the live mixer."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from mpclab.engine import Engine
from mpclab.model import Project

SR = 48_000


class _Clip:
    def __init__(self, cid, duration):
        self.id, self.name, self.duration = cid, "burst", duration


class _Library:
    """Just enough library for the engine: one decaying tone burst."""

    def __init__(self):
        t = np.arange(SR // 2) / SR
        burst = (np.sin(2 * np.pi * 400 * t) * np.exp(-t * 25)).astype(np.float32)
        self._audio = np.stack([burst, burst], axis=1)
        self.clips = {"burst": _Clip("burst", 0.5)}

    def audio(self, clip_id):
        return self._audio

    def peaks(self, clip_id):
        return None


def _windows(audio, seconds=0.25):
    step = int(SR * seconds)
    return [float(np.abs(audio[i : i + step]).max()) for i in range(0, len(audio) - step, step)]


class OfflineFXTest(unittest.TestCase):
    def setUp(self):
        self.project = Project(bpm=120.0)
        self.project.pads[0].sample_id = "burst"
        self.project.pads[0].end = 0.4
        self.project.pads[0].track = 0
        pattern = self.project.pattern()
        pattern.bars = 1
        pattern.steps[0] = {0: 1.0}
        self.engine = Engine(_Library())
        self.engine.project = self.project

    def _bounce(self):
        return self.engine.render_offline(mode="pattern", tail=4.0)

    def test_a_dry_bounce_is_one_hit_and_then_silence(self):
        levels = _windows(self._bounce())
        self.assertGreater(levels[0], 0.1)
        self.assertLess(max(levels[2:]), 1e-4)

    def test_reverb_send_adds_a_decaying_tail(self):
        self.project.tracks[0].fx.send_reverb = 0.9
        self.project.reverb_fx.size = 0.8
        levels = _windows(self._bounce())
        self.assertGreater(levels[2], 0.02)  # tail is there
        self.assertLess(levels[8], levels[2])  # and it is going away
        self.assertLess(max(levels[-4:]), 1e-3)  # and it does go away

    def test_delay_send_repeats_on_the_beat(self):
        self.project.tracks[0].fx.send_delay = 0.9
        self.project.delay_fx.sync = "1/4"
        self.project.delay_fx.feedback = 0.6
        self.project.delay_fx.ping_pong = False
        self.project.delay_fx.level = 1.0
        levels = _windows(self._bounce())
        # A 1/4 at 120 bpm is 0.5 s: every other 0.25 s window carries a repeat.
        repeats = levels[2::2][:4]
        self.assertGreater(repeats[0], 0.05)
        for loud, quiet in zip(repeats, repeats[1:], strict=False):
            self.assertLess(quiet, loud)
        for odd in levels[3:10:2]:
            self.assertLess(odd, 0.01)

    def test_track_eq_reaches_the_bounce(self):
        flat = self._bounce()
        self.project.tracks[0].fx.low = 10.0
        lifted = self._bounce()
        band = slice(0, 300)  # the bottom of the spectrum
        self.assertGreater(
            float(np.abs(np.fft.rfft(lifted[:SR, 0]))[band].sum()),
            float(np.abs(np.fft.rfft(flat[:SR, 0]))[band].sum()),
        )

    def test_the_limiter_still_has_the_last_word(self):
        self.project.tracks[0].fx.drive = 1.0
        self.project.tracks[0].fx.makeup = 18.0
        self.project.tracks[0].fx.comp = True
        self.project.master_fx.glue = True
        self.project.master_fx.drive = 1.0
        out = self._bounce()
        self.assertTrue(np.isfinite(out).all())
        self.assertLessEqual(float(np.abs(out).max()), 10 ** (-1.0 / 20) + 1e-3)

    def test_an_untouched_project_bounces_exactly_as_before(self):
        before = self._bounce()
        self.project.tracks[0].fx.low = 0.0  # still all defaults
        np.testing.assert_allclose(before, self._bounce(), atol=1e-7)


if __name__ == "__main__":
    unittest.main()
