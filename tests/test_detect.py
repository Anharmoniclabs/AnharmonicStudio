"""Detector tests against a synthesised pattern whose contents are known.

Real music has no labels, so the classifier is checked on drums built from
first principles — a low sine thump, a noise burst, a high-passed tick — laid
out on a grid at a fixed tempo. Everything the CHOP tab claims to find (hits,
their categories, the tempo, the loop, the drop) has to fall out of that.
"""

from __future__ import annotations

import unittest

import numpy as np

from mpclab import detect
from mpclab.dsp import SR, detect_bpm

BPM = 120.0
BEAT = 60.0 / BPM


def _env(n: int, decay: float, attack: float = 0.002) -> np.ndarray:
    """Exponential decay behind a short attack ramp.

    Real drums do not start on a vertical edge, and a vertical edge is a click
    — broadband by definition, and therefore unclassifiable.
    """
    env = np.exp(-np.arange(n) / (SR * decay))
    rise = max(1, int(SR * attack))
    env[:rise] *= np.linspace(0.0, 1.0, rise)
    return env.astype(np.float32)


def _band_noise(n: int, lo: float, hi: float, seed: int) -> np.ndarray:
    """Noise confined to one band, so each test drum owns a known region."""
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    spectrum[(freqs < lo) | (freqs > hi)] = 0.0
    out = np.fft.irfft(spectrum, n).astype(np.float32)
    return out / (np.abs(out).max() + 1e-9)


def kick(dur: float = 0.28) -> np.ndarray:
    """Low sine thump: all of its energy under 90 Hz."""
    n = int(SR * dur)
    tone = np.sin(2 * np.pi * 55.0 * np.arange(n) / SR).astype(np.float32)
    return (tone * _env(n, 0.05) * 0.95).astype(np.float32)


def snare(dur: float = 0.20) -> np.ndarray:
    """Mid-band noise with a little body — broad, but not a hat and not a kick."""
    n = int(SR * dur)
    body = np.sin(2 * np.pi * 320.0 * np.arange(n) / SR).astype(np.float32)
    return ((_band_noise(n, 700.0, 6000.0, 7) * 0.9 + body * 0.3) * _env(n, 0.045) * 0.75).astype(
        np.float32
    )


def hat(dur: float = 0.05) -> np.ndarray:
    """Top-band tick, nothing below 7 kHz."""
    n = int(SR * dur)
    return (_band_noise(n, 7000.0, 16000.0, 11) * _env(n, 0.01) * 0.55).astype(np.float32)


def place(track: np.ndarray, sound: np.ndarray, at: float) -> None:
    i = int(at * SR)
    end = min(len(track), i + len(sound))
    if end > i:
        track[i:end] += sound[: end - i]


# Boom-bap rather than four-on-the-floor: no two drums share a position, so
# every detected onset has exactly one right answer.
KICK_BEATS = (0.0, 2.0)
SNARE_BEATS = (1.0, 3.0)
HAT_BEATS = (0.5, 1.5, 2.5, 3.5)


def build_pattern(bars: int = 8, quiet_bars: int = 0) -> np.ndarray:
    total = int(bars * 4 * BEAT * SR) + SR
    track = np.zeros(total, dtype=np.float32)
    k, s, h = kick(), snare(), hat()
    for bar in range(bars):
        base = bar * 4 * BEAT
        gain = 0.2 if bar < quiet_bars else 1.0
        for beat in KICK_BEATS:
            place(track, k * gain, base + beat * BEAT)
        for beat in SNARE_BEATS:
            place(track, s * gain, base + beat * BEAT)
        for beat in HAT_BEATS:
            place(track, h * gain, base + beat * BEAT)
    return track


class ClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audio = build_pattern()
        cls.spec = detect.spectra(cls.audio)
        cls.hits = detect.find_hits(cls.audio, spec=cls.spec)

    def _kinds_at(self, position: float, tol: float = 0.06) -> list[str]:
        """Categories of every hit landing near a beat position in any bar."""
        out = []
        for hit in self.hits:
            offset = hit.start % (4 * BEAT)
            if min(abs(offset - position), 4 * BEAT - abs(offset - position)) <= tol:
                out.append(hit.kind)
        return out

    def test_every_drum_is_found(self):
        # 8 bars × (2 kicks + 2 snares + 4 hats) = 64 scheduled drums.
        self.assertGreaterEqual(len(self.hits), 60)

    def test_downbeat_is_a_kick(self):
        kinds = self._kinds_at(0.0)
        self.assertTrue(kinds, "no hit detected on the downbeat")
        self.assertEqual(max(set(kinds), key=kinds.count), "kick")

    def test_backbeat_is_a_snare(self):
        kinds = self._kinds_at(BEAT)
        self.assertTrue(kinds, "no hit detected on the backbeat")
        self.assertIn(max(set(kinds), key=kinds.count), ("snare", "clap"))

    def test_offbeat_eighth_is_a_hat(self):
        kinds = self._kinds_at(0.5 * BEAT)
        self.assertTrue(kinds, "no hit detected on the offbeat")
        self.assertEqual(max(set(kinds), key=kinds.count), "hat")

    def test_hits_are_short_and_scored(self):
        for hit in self.hits:
            self.assertGreater(hit.length, 0.0)
            self.assertLessEqual(hit.length, 0.95)
            self.assertGreaterEqual(hit.score, 0.0)
            self.assertLessEqual(hit.score, 1.0)

    def test_categories_reach_the_pad_layout(self):
        grouped = detect.best_hits(self.hits)
        placed = detect.layout_hits(grouped)
        self.assertIn("kick", grouped)
        self.assertTrue(placed, "nothing was laid out on the pads")
        self.assertLessEqual(max(placed), 15)
        self.assertEqual(placed[0].kind, "kick")


class TempoAndStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audio = build_pattern(bars=12, quiet_bars=4)
        cls.spec = detect.spectra(cls.audio)

    def test_tempo_is_recovered(self):
        found = detect_bpm(self.audio)
        self.assertAlmostEqual(found, BPM, delta=2.0)

    def test_beat_grid_lands_on_the_beat(self):
        from mpclab.dsp import onset_envelope

        phase, downbeat = detect.beat_grid(
            onset_envelope(self.audio),
            self.spec.fps,
            BPM,
            accent=self.spec.band_envelope(detect.SUB, detect.LOW),
        )
        self.assertLess(min(phase % BEAT, BEAT - phase % BEAT), 0.05)
        self.assertLess(min(downbeat % BEAT, BEAT - downbeat % BEAT), 0.05)

    def test_repeating_bar_is_offered_as_a_loop(self):
        loops = detect.find_loops(self.audio, bpm=BPM, spec=self.spec, bars=(1,))
        self.assertTrue(loops, "an eight-times-repeated bar was not found")
        best = loops[0]
        self.assertAlmostEqual(best.length, 4 * BEAT, delta=BEAT)
        self.assertGreaterEqual(best.detail["repeats"], 3)
        # The loud section is the one worth sampling.
        self.assertGreater(best.start, 4 * 4 * BEAT - BEAT)

    def test_level_step_is_reported_as_a_drop(self):
        drops = detect.find_drops(self.audio, bpm=BPM, spec=self.spec)
        self.assertTrue(drops, "the four-bar level step was not found")
        self.assertAlmostEqual(drops[0].start, 4 * 4 * BEAT, delta=2 * BEAT)

    def test_scan_returns_every_section(self):
        res = detect.scan(self.audio, bpm=BPM)
        for key in ("bpm", "phase", "downbeat", "onsets", "hits", "by_kind", "loops", "drops"):
            self.assertIn(key, res)


class QuietFileTests(unittest.TestCase):
    def test_silence_does_not_raise(self):
        quiet = np.zeros(SR * 3, dtype=np.float32)
        res = detect.scan(quiet)
        self.assertEqual(res["hits"], [])
        self.assertEqual(res["loops"], [])

    def test_very_short_file_is_handled(self):
        blip = np.zeros(2048, dtype=np.float32)
        blip[100:200] = 0.5
        res = detect.scan(blip)
        self.assertIsInstance(res["hits"], list)


if __name__ == "__main__":
    unittest.main()


class OnsetRefinementTests(unittest.TestCase):
    def test_quiet_attacks_survive_a_much_louder_hit_later(self):
        audio = np.zeros(SR * 6, dtype=np.float32)
        expected = (1.0, 1.5, 2.0, 2.5)
        for at in expected:
            place(audio, hat() * 0.002, at)
        place(audio, kick(), 4.0)
        result = detect.scan(audio, bpm=120)
        for at in expected:
            self.assertTrue(any(abs(t - at) < 0.05 for t in result["onsets"]), at)
        self.assertTrue(all(t > 0.9 for t in result["onsets"]))

    def test_silence_has_no_automatic_cut(self):
        result = detect.scan(np.zeros(SR, dtype=np.float32), bpm=120)
        self.assertEqual(result["onsets"], [])

    def test_opposite_polarity_stereo_keeps_detectable_audio(self):
        audio = build_pattern(bars=1)
        mono = detect.analysis_mono(np.column_stack((audio, -audio)))
        np.testing.assert_array_equal(mono, audio)
        self.assertGreaterEqual(len(detect.scan(mono, bpm=120)["onsets"]), 8)

    def test_sensitivity_adds_quiet_peaks_without_splitting_a_plateau(self):
        from mpclab.dsp import pick_peaks

        envelope = np.full(200, 0.1, dtype=np.float32)
        envelope[40:43] = 0.5
        envelope[50] = 0.13
        fewer = pick_peaks(envelope, 100, sensitivity=0.2)
        more = pick_peaks(envelope, 100, sensitivity=2.5)
        self.assertGreater(len(more), len(fewer))
        self.assertEqual(sum(40 <= p <= 42 for p in more), 1)
