"""Audio-callback integration tests without opening a PortAudio device."""

from __future__ import annotations

import sys
import types
import unittest

import numpy as np

sys.modules["sounddevice"] = types.SimpleNamespace(OutputStream=object)

from mpclab.engine import Engine, MAX_SYNTH_VOICES


class _Library:
    def audio(self, _sample_id):
        return None

    def reversed_audio(self, _sample_id):
        return None


class EngineSynthTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(_Library(), sample_rate=8000, blocksize=128)

    def callback(self):
        out = np.zeros((128, 2), dtype=np.float32)
        self.engine._callback(out, 128, None, False)
        return out

    def test_note_commands_render_through_master(self):
        self.engine.synth_note_on(60, 0.9)
        out = self.callback()
        self.assertGreater(float(np.sqrt(np.mean(out * out))), 0.0001)
        self.assertTrue(np.isfinite(out).all())

    def test_arp_schedules_held_chord(self):
        self.engine.project.bpm = 120
        self.engine.project.arp.enabled = True
        for note in (60, 64, 67):
            self.engine.synth_note_on(note)
        out = self.callback()
        self.assertGreater(float(np.sqrt(np.mean(out * out))), 0.0001)
        self.assertEqual(self.engine.arp_state.held, {60, 64, 67})

    def test_voice_stealing_caps_polyphony(self):
        for note in range(48, 48 + MAX_SYNTH_VOICES + 5):
            self.engine.synth_note_on(note)
        self.callback()
        self.assertLessEqual(len(self.engine.synth_voices), MAX_SYNTH_VOICES)


if __name__ == "__main__":
    unittest.main()
