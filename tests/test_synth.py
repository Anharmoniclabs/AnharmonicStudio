"""DSP, patch, persistence, and note-order tests for the analog instrument."""

from __future__ import annotations

import unittest

import numpy as np

from mpclab.model import ArpSettings, Project, SynthPatch
from mpclab.synth import (
    ArpState,
    PATCHES,
    SynthVoice,
    midi_to_hz,
    patch_copy,
    render_patch,
)


class SynthTests(unittest.TestCase):
    def test_middle_a_frequency(self):
        self.assertAlmostEqual(midi_to_hz(69), 440.0)

    def test_factory_patches_are_independent(self):
        patch = patch_copy("Copper Pluck")
        patch.cutoff = 99
        self.assertNotEqual(patch.cutoff, PATCHES["Copper Pluck"].cutoff)
        self.assertGreaterEqual(len(PATCHES), 8)

    def test_voice_is_stereo_finite_and_releases(self):
        patch = SynthPatch(
            attack=0.001,
            decay=0.01,
            sustain=0.5,
            release=0.01,
            cutoff=1200,
            spread=0.8,
            volume=0.35,
        )
        voice = SynthVoice(note=60, velocity=0.9, sample_rate=8000)
        block = np.zeros((512, 2), dtype=np.float32)
        voice.render(block, patch)
        self.assertTrue(np.isfinite(block).all())
        self.assertGreater(float(np.sqrt(np.mean(block * block))), 0.001)
        self.assertFalse(np.array_equal(block[:, 0], block[:, 1]))
        voice.note_off(patch.release)
        voice.render(np.zeros((512, 2), dtype=np.float32), patch)
        self.assertTrue(voice.dead)

    def test_patch_can_be_printed_to_sample_audio(self):
        patch = patch_copy("Copper Pluck")
        audio = render_patch(patch, 48, 0.05, sample_rate=8000)
        self.assertEqual(audio.ndim, 2)
        self.assertEqual(audio.shape[1], 2)
        self.assertGreater(len(audio), 400)
        self.assertTrue(np.isfinite(audio).all())
        self.assertGreater(float(np.max(np.abs(audio))), 0.001)

    def test_arp_orders_notes_and_octaves(self):
        arp = ArpState()
        for note in (67, 60, 64):
            arp.press(note)
        settings = ArpSettings(mode="up", octaves=2)
        self.assertEqual(arp.sequence(settings), [60, 64, 67, 72, 76, 79])
        settings.mode = "down"
        self.assertEqual(arp.sequence(settings), [79, 76, 72, 67, 64, 60])
        settings.mode = "up/down"
        self.assertEqual(arp.sequence(settings), [60, 64, 67, 72, 76, 79, 76, 72, 67, 64])

    def test_synth_and_arp_round_trip_in_project(self):
        project = Project()
        project.synth = patch_copy("Broken Carousel")
        project.arp = ArpSettings(enabled=True, rate_beats=0.5, mode="random", octaves=3, gate=0.44)
        loaded = Project.from_dict(project.to_dict())
        self.assertEqual(loaded.synth.name, "Broken Carousel")
        self.assertEqual(loaded.synth.detune, 22)
        self.assertTrue(loaded.arp.enabled)
        self.assertEqual(loaded.arp.mode, "random")
        self.assertEqual(loaded.arp.octaves, 3)


if __name__ == "__main__":
    unittest.main()
