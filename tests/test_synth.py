"""DSP, patch, persistence, and note-order tests for the analog instrument."""

from __future__ import annotations

import unittest

import numpy as np

import mpclab.synth as synth_module
from mpclab.model import ArpSettings, Project, SynthPatch
from mpclab.synth import (
    ArpState,
    PATCHES,
    SynthVoice,
    midi_to_hz,
    patch_copy,
    render_patch,
)


def _render_fallback_partition(chunks, *, start_offset=0, gate_frames=1049, note_off_at=None):
    """Render a noisy voice with no native acceleration through given blocks."""
    voice = SynthVoice(61, 0.83, 48000, start_offset=start_offset, gate_frames=gate_frames)
    patch = patch_copy("Broken Carousel")
    patch.noise = 0.25
    blocks = []
    native = synth_module.NATIVE
    synth_module.NATIVE = None
    try:
        for size in chunks:
            output = np.zeros((size, 2), dtype=np.float32)
            voice.render(output, patch)
            blocks.append(output)
            if note_off_at is not None and voice.age == note_off_at:
                voice.note_off(patch.release)
    finally:
        synth_module.NATIVE = native
    return np.concatenate(blocks), voice


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

    def test_fallback_half_rate_filter_is_partition_invariant(self):
        total = 4097
        whole, whole_voice = _render_fallback_partition((total,), start_offset=17)
        # Mixed odd/even calls include several one-frame callbacks and expire
        # the automatic gate mid-layout.
        chunks = (1, 127, 256, 513, 1, 255, 512, 128, 1024, 1280)
        partitioned, voice = _render_fallback_partition(chunks, start_offset=17)
        np.testing.assert_allclose(partitioned, whole, atol=3e-9, rtol=0)
        self.assertEqual(
            (voice.age, voice.stage, voice.dead),
            (whole_voice.age, whole_voice.stage, whole_voice.dead),
        )

        # Every frame as an independent callback is the strongest pair-boundary
        # stress case. The seeded stereo noise stream must also remain exact.
        one_by_one, _ = _render_fallback_partition((1,) * 257, gate_frames=None)
        compact, _ = _render_fallback_partition((257,), gate_frames=None)
        np.testing.assert_array_equal(one_by_one, compact)
        self.assertFalse(np.array_equal(compact[:, 0], compact[:, 1]))

    def test_short_callback_retains_start_offset_and_causal_filter_latency(self):
        patch = patch_copy("Broken Carousel")
        native = synth_module.NATIVE
        synth_module.NATIVE = None
        try:
            voice = SynthVoice(61, 0.83, 48000, start_offset=17, gate_frames=None)
            voice.render(np.zeros((1, 2), dtype=np.float32), patch)
            self.assertEqual((voice.start_offset, voice.age), (16, 0))
            voice.render(np.zeros((16, 2), dtype=np.float32), patch)
            self.assertEqual((voice.start_offset, voice.age), (0, 0))
        finally:
            synth_module.NATIVE = native

        onset, _ = _render_fallback_partition((2,), gate_frames=None)
        # The first source frame waits for its pair; its filter value is
        # available on the second frame (one-frame causal startup latency).
        np.testing.assert_array_equal(onset[0], np.zeros(2, dtype=np.float32))
        self.assertGreater(float(np.max(np.abs(onset[1]))), 0.0)

    def test_fallback_half_rate_release_across_odd_boundary_and_reset(self):
        # The first run ends on an odd source frame, then note-off occurs
        # before a differently sized callback completes that pending pair.
        baseline, _ = _render_fallback_partition((513, 3584), gate_frames=None, note_off_at=513)
        chunks = (1, 127, 255, 130, 513, 1024, 2047)
        partitioned, voice = _render_fallback_partition(chunks, gate_frames=None, note_off_at=513)
        np.testing.assert_allclose(partitioned, baseline, atol=3e-9, rtol=0)
        self.assertEqual(voice.stage, "release")

        first, _ = _render_fallback_partition((1, 127, 513, 255, 1024), start_offset=3)
        second, _ = _render_fallback_partition((1, 127, 513, 255, 1024), start_offset=3)
        np.testing.assert_array_equal(first, second)


if __name__ == "__main__":
    unittest.main()
