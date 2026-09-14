"""Pad voice stealing stays source-aware across all four banks."""

from __future__ import annotations

import sys
import types
import unittest

import numpy as np

sys.modules.setdefault("sounddevice", types.SimpleNamespace(OutputStream=object))

from mpclab.engine import Engine, FADE, MAX_PAD_VOICES
from mpclab.model import Pad, Pattern, Clip


SR = 8000


class _Library:
    def __init__(self):
        self.samples = {
            "song": np.full((SR, 2), 0.025, dtype=np.float32),
            "other": np.full((SR, 2), 0.025, dtype=np.float32),
        }

    def audio(self, sample_id):
        return self.samples.get(sample_id)

    def reversed_audio(self, sample_id):
        data = self.audio(sample_id)
        return None if data is None else data[::-1]


class CrossBankChokeTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(_Library(), sample_rate=SR, blocksize=128)
        self.engine.project.pads[0] = Pad(sample_id="song")
        self.engine.project.pads[16] = Pad(sample_id="song")  # bank B, pad 1

    def test_cut_source_steals_the_same_sample_from_another_bank(self):
        self.engine.project.self_choke = True
        self.engine._spawn(self.engine.project.pads[0], 0, 1.0)
        previous = self.engine.voices[-1]
        self.engine._spawn(self.engine.project.pads[16], 16, 1.0)

        self.assertEqual(previous.length, int(FADE * SR))
        self.assertEqual(self.engine.voices[-1].pad_index, 16)

    def test_cut_source_off_allows_cross_bank_slices_to_layer(self):
        self.engine.project.self_choke = False
        self.engine._spawn(self.engine.project.pads[0], 0, 1.0)
        previous = self.engine.voices[-1]
        natural = previous.length
        self.engine._spawn(self.engine.project.pads[16], 16, 1.0)
        self.assertEqual(previous.length, natural)

    def test_unrelated_sources_still_layer_when_cut_source_is_on(self):
        self.engine.project.self_choke = True
        self.engine.project.pads[16].sample_id = "other"
        self.engine._spawn(self.engine.project.pads[0], 0, 1.0)
        previous = self.engine.voices[-1]
        natural = previous.length
        self.engine._spawn(self.engine.project.pads[16], 16, 1.0)
        self.assertEqual(previous.length, natural)

    def test_choke_groups_cut_different_sources_across_banks(self):
        self.engine.project.pads[0].choke = 3
        self.engine.project.pads[16] = Pad(sample_id="other", choke=3)
        self.engine._spawn(self.engine.project.pads[0], 0, 1.0)
        previous = self.engine.voices[-1]
        self.engine._spawn(self.engine.project.pads[16], 16, 1.0)
        self.assertEqual(previous.length, int(FADE * SR))

    def test_live_mpc_cuts_itself_while_another_pattern_keeps_playing(self):
        engine = self.engine
        engine.project.self_choke = True
        recorded = engine.project.pattern()
        recorded.steps = {0: {0: 1.0}}
        engine.project.rows[0].clips = [Clip(kind="pattern", ref=recorded.id, length_beats=4)]
        next_pattern = Pattern(name="Live instrument part")
        engine.project.patterns.append(next_pattern)
        engine.project.current_pattern = next_pattern.id
        engine.mode = "song"
        engine.play(0)
        out = np.zeros((128, 2), dtype=np.float32)
        engine._callback(out, 128, None, False)
        backing = engine.voices[0]
        natural = backing.length
        self.assertFalse(backing.live_trigger)
        engine.trigger_pad(16)
        engine._callback(out, 128, None, False)
        first_tap = next(voice for voice in engine.voices if voice.live_trigger)
        engine.trigger_pad(0)
        engine._callback(out, 128, None, False)
        self.assertEqual(backing.length, natural)
        self.assertFalse(backing.dead)
        self.assertLess(first_tap.length, natural)
        self.assertTrue(any(voice.live_trigger and voice.pad_index == 0 for voice in engine.voices))
        self.assertEqual(recorded.steps, {0: {0: 1.0}})
        self.assertEqual(next_pattern.steps, {})

    def test_sequence_hits_do_not_cut_live_instrument_but_cut_other_sequence_hits(self):
        engine = self.engine
        engine.project.self_choke = True
        engine._spawn(engine.project.pads[0], 0, 1.0)
        live = engine.voices[-1]
        natural = live.length
        engine._spawn(engine.project.pads[0], 0, 1.0, live_trigger=False)
        recorded = engine.voices[-1]
        engine._spawn(engine.project.pads[16], 16, 1.0, live_trigger=False)
        self.assertEqual(live.length, natural)
        self.assertEqual(recorded.length, int(FADE * SR))

    def test_gate_release_and_choke_group_do_not_cut_recorded_pad(self):
        engine = self.engine
        engine.project.pads[0] = Pad(sample_id="song", mode="gate", choke=3)
        engine.project.pads[16] = Pad(sample_id="other", mode="gate", choke=3)
        engine._spawn(engine.project.pads[0], 0, 1.0, live_trigger=False)
        recorded = engine.voices[-1]
        natural = recorded.length
        engine.trigger_pad(0)
        engine._process_commands()
        live = engine.voices[-1]
        engine.release_pad(0)
        engine._process_commands()
        self.assertLess(live.length, natural)
        self.assertEqual(recorded.length, natural)
        engine.trigger_pad(16)
        engine._process_commands()
        self.assertEqual(recorded.length, natural)

    def test_live_polyphony_limit_never_steals_recorded_voices(self):
        engine = self.engine
        for _ in range(MAX_PAD_VOICES):
            engine._spawn(engine.project.pads[0], 0, 1.0, live_trigger=False)
        recorded = list(engine.voices)
        engine.trigger_pad(16)
        engine._process_commands()
        self.assertEqual(engine.voices, recorded)
        self.assertTrue(all(voice.length == SR for voice in recorded))

    def test_offline_bounce_obeys_cross_bank_source_cut(self):
        pattern = self.engine.project.pattern()
        pattern.bars = 1
        pattern.div = 4
        pattern.steps = {0: {0: 1.0}, 16: {4: 1.0}}
        self.engine.project.bpm = 120.0

        self.engine.project.self_choke = False
        layered = self.engine.render_offline(mode="pattern", tail=0.0)
        self.engine.project.self_choke = True
        cut = self.engine.render_offline(mode="pattern", tail=0.0)

        # Between 0.6 and 0.8 seconds both one-second voices overlap only in
        # the uncut render. Keep the level low so the mastering stage is linear.
        span = slice(int(0.6 * SR), int(0.8 * SR))
        layered_rms = float(np.sqrt(np.mean(layered[span] ** 2)))
        cut_rms = float(np.sqrt(np.mean(cut[span] ** 2)))
        self.assertGreater(layered_rms, cut_rms * 1.6)


if __name__ == "__main__":
    unittest.main()
