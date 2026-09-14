"""The mix effects: block processing must equal the maths it stands in for."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from mpclab import fx
from mpclab.model import DelayFX, MasterFX, Project, ReverbFX, TrackFX

SR = fx.SR


def _impulse(frames=4096, channels=2):
    sig = np.zeros((frames, channels), dtype=np.float32)
    sig[0] = 1.0
    return sig


def _spectrum_db(signal):
    return 20.0 * np.log10(np.abs(np.fft.rfft(signal)) + 1e-12)


def _at(spec, hz, frames=4096):
    freqs = np.fft.rfftfreq(frames, 1.0 / SR)
    return float(spec[int(np.argmin(np.abs(freqs - hz)))])


def _track_response(track_fx, frames=4096):
    chain = fx.TrackChain()
    sig = _impulse(frames)
    chain.process(sig, track_fx)
    return _spectrum_db(sig[:, 0])


class BlockConvolverTest(unittest.TestCase):
    def test_matches_direct_convolution_across_ragged_blocks(self):
        rng = np.random.default_rng(3)
        sizes = [1024, 512, 1024, 700, 837]
        total = sum(sizes)
        ir = (rng.standard_normal(1536) * np.exp(-np.arange(1536) / 220.0)).astype(np.float32)
        x = rng.standard_normal((total, 2)).astype(np.float32)

        conv = fx.BlockConvolver(ir)
        out, at = [], 0
        for n in sizes:
            out.append(conv.process(x[at : at + n].copy()))
            at += n
        got = np.concatenate(out)
        want = np.stack([np.convolve(x[:, ch], ir)[:total] for ch in range(2)], axis=1)
        self.assertLess(np.abs(got - want).max(), 1e-4)

    def test_adds_no_latency(self):
        ir = np.zeros(64, dtype=np.float32)
        ir[0] = 1.0
        conv = fx.BlockConvolver(ir)
        block = _impulse(256)
        conv.process(block)
        self.assertAlmostEqual(float(block[0, 0]), 1.0, places=5)


class OnePoleTest(unittest.TestCase):
    def test_matches_the_recursion_it_replaces(self):
        rng = np.random.default_rng(11)
        sizes = [1024, 333, 1024]
        total = sum(sizes)
        x = rng.standard_normal((total, 2)).astype(np.float32)
        b = 0.995

        pole = fx.OnePole(2)
        out, at = [], 0
        for n in sizes:
            out.append(pole.process(x[at : at + n].copy(), b))
            at += n
        got = np.concatenate(out)

        want = np.zeros((total, 2))
        state = np.zeros(2)
        for i in range(total):
            state = (1 - b) * x[i] + b * state
            want[i] = state
        self.assertLess(np.abs(got - want).max(), 1e-5)


class ToneTest(unittest.TestCase):
    def test_low_shelf_lifts_only_the_low_end(self):
        spec = _track_response(TrackFX(low=6.0))
        self.assertGreater(_at(spec, 50), 5.0)
        self.assertLess(abs(_at(spec, 10_000)), 0.5)

    def test_high_shelf_cuts_only_the_top(self):
        spec = _track_response(TrackFX(high=-8.0))
        self.assertLess(_at(spec, 12_000), -6.0)
        self.assertLess(abs(_at(spec, 100)), 0.5)

    def test_mid_bell_peaks_where_it_is_tuned(self):
        spec = _track_response(TrackFX(mid=9.0, mid_freq=1_000.0))
        self.assertGreater(_at(spec, 1_000), 8.0)
        self.assertLess(_at(spec, 100), 1.0)

    def test_lowpass_and_highpass_pass_the_right_side(self):
        low = _track_response(TrackFX(filter_type="lowpass", cutoff=500.0))
        self.assertLess(low[0] - low[0], 0.001)  # no NaNs in the response
        self.assertGreater(_at(low, 100), -1.0)
        self.assertLess(_at(low, 5_000), -25.0)

        high = _track_response(TrackFX(filter_type="highpass", cutoff=800.0))
        self.assertLess(_at(high, 100), -25.0)
        self.assertGreater(_at(high, 5_000), -1.0)

    def test_defaults_leave_the_audio_alone(self):
        chain = fx.TrackChain()
        rng = np.random.default_rng(5)
        block = (rng.standard_normal((1024, 2)) * 0.2).astype(np.float32)
        before = block.copy()
        chain.process(block, TrackFX())
        np.testing.assert_allclose(block, before, atol=1e-6)


class DynamicsTest(unittest.TestCase):
    def _tone(self, amplitude, seconds=1.0):
        t = np.arange(int(SR * seconds)) / SR
        wave = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        return np.repeat(wave[:, None], 2, axis=1)

    def _run(self, signal, **kwargs):
        comp = fx.Compressor()
        out = [
            comp.process(signal[i : i + 1024].copy(), **kwargs) for i in range(0, len(signal), 1024)
        ]
        return np.concatenate(out), comp

    def test_loud_signal_is_pulled_down(self):
        loud = self._tone(0.9)
        out, comp = self._run(loud, threshold_db=-24.0, ratio=6.0, attack=0.005, release=0.1)
        self.assertLess(np.abs(out[-8000:]).max(), 0.25)
        self.assertGreater(comp.gain_reduction_db, 10.0)

    def test_quiet_signal_passes_untouched(self):
        quiet = self._tone(0.02)
        out, comp = self._run(quiet, threshold_db=-24.0, ratio=6.0, attack=0.005, release=0.1)
        self.assertAlmostEqual(float(np.abs(out[-8000:]).max()), 0.02, places=3)
        self.assertLess(comp.gain_reduction_db, 0.5)

    def test_drive_is_bounded_and_finite(self):
        block = np.ones((512, 2), dtype=np.float32) * 4.0
        fx.saturate(block, 1.0)
        self.assertTrue(np.isfinite(block).all())
        self.assertLess(np.abs(block).max(), 1.0)


class SendTest(unittest.TestCase):
    def test_delay_echoes_on_the_beat_division(self):
        send = fx.DelaySend()
        settings = DelayFX(sync="1/8", feedback=0.0, level=1.0, ping_pong=False)
        block = _impulse(1024)
        block[:, 1] = 0.0
        tail = [send.process(block, settings, 120.0).copy()]
        for _ in range(30):
            tail.append(send.process(np.zeros((1024, 2), dtype=np.float32), settings, 120.0).copy())
        tail = np.concatenate(tail)
        peak_ms = int(np.argmax(np.abs(tail[:, 0]))) / SR * 1000.0
        self.assertAlmostEqual(peak_ms, 250.0, delta=1.0)  # 1/8 at 120 bpm

    def test_delay_never_reads_what_the_same_block_writes(self):
        send = fx.DelaySend()
        settings = DelayFX(sync="1/16")
        for bpm in (40.0, 240.0):
            self.assertGreaterEqual(send.delay_samples(settings, bpm, 1024), 1024)

    def test_reverb_decays_and_stays_finite(self):
        send = fx.ReverbSend()
        settings = ReverbFX(size=0.7)
        blocks = [send.process(_impulse(1024), settings).copy()]
        for _ in range(90):
            blocks.append(send.process(np.zeros((1024, 2), dtype=np.float32), settings).copy())
        tail = np.concatenate(blocks)
        self.assertTrue(np.isfinite(tail).all())
        window = SR // 5
        peaks = [
            float(np.abs(tail[i : i + window]).max()) for i in range(0, len(tail) - window, window)
        ]
        self.assertGreater(peaks[0], 0.01)
        self.assertLess(peaks[-1], peaks[1] * 0.5)
        for a, b in zip(peaks[1:], peaks[2:], strict=False):
            self.assertLessEqual(b, a)


class RackTest(unittest.TestCase):
    def test_send_buses_come_back_cleared(self):
        rack = fx.MixRack(8)
        delay, reverb = rack.send_buffers(1024)
        delay += 1.0
        delay, reverb = rack.send_buffers(1024)
        self.assertEqual(float(np.abs(delay).max()), 0.0)
        self.assertEqual(float(np.abs(reverb).max()), 0.0)

    def test_master_chain_defaults_are_transparent(self):
        rack = fx.MixRack(2)
        rng = np.random.default_rng(2)
        block = (rng.standard_normal((1024, 2)) * 0.1).astype(np.float32)
        before = block.copy()
        rack.master.process(block, MasterFX())
        np.testing.assert_allclose(block, before, atol=1e-6)


class ProjectFXTest(unittest.TestCase):
    def test_fresh_project_reports_no_active_effects(self):
        proj = Project()
        self.assertFalse(any(t.fx.active for t in proj.tracks))
        self.assertFalse(any(t.fx.sends_active for t in proj.tracks))
        self.assertFalse(proj.master_fx.active)

    def test_settings_survive_a_save_and_load(self):
        proj = Project()
        proj.tracks[2].fx.low = 4.5
        proj.tracks[2].fx.comp = True
        proj.tracks[2].fx.send_reverb = 0.4
        proj.delay_fx.sync = "1/16"
        proj.master_fx.glue = True
        again = Project.from_dict(proj.to_dict())
        self.assertAlmostEqual(again.tracks[2].fx.low, 4.5)
        self.assertTrue(again.tracks[2].fx.comp)
        self.assertAlmostEqual(again.tracks[2].fx.send_reverb, 0.4)
        self.assertEqual(again.delay_fx.sync, "1/16")
        self.assertTrue(again.master_fx.glue)
        self.assertTrue(again.tracks[2].fx.active)

    def test_a_project_saved_before_effects_existed_still_loads(self):
        old = {
            "name": "legacy",
            "bpm": 92.0,
            "tracks": [{"name": "DRUMS", "gain": 0.8, "pan": 0.0}],
        }
        proj = Project.from_dict(old)
        self.assertEqual(proj.tracks[0].name, "DRUMS")
        self.assertFalse(proj.tracks[0].fx.active)
        self.assertEqual(proj.delay_fx.sync, "1/8")


if __name__ == "__main__":
    unittest.main()
