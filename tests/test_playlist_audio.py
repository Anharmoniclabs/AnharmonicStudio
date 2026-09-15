"""Playlist audio blocks: persistent trim/loop state and engine voices."""

from __future__ import annotations

import unittest

import numpy as np

from mpclab.engine import Engine
from mpclab.model import Clip, Project


class MemoryLibrary:
    def __init__(self):
        ramp = np.arange(40, dtype=np.float32)[:, None]
        self.data = np.repeat(ramp, 2, axis=1)
        self.reversed = np.ascontiguousarray(self.data[::-1])

    def audio(self, _ref):
        return self.data

    def reversed_audio(self, _ref):
        return self.reversed


class PlaylistAudioTests(unittest.TestCase):
    def setUp(self):
        self.library = MemoryLibrary()
        self.engine = Engine(self.library, sample_rate=20, blocksize=16)
        self.engine.project.bpm = 60.0

    def test_clip_controls_round_trip_with_project(self):
        clip = Clip(
            kind="audio",
            ref="wav",
            offset=0.25,
            source_length=0.5,
            loop=True,
            reverse=True,
            gain=0.75,
            track=6,
            mute=True,
        )
        project = Project(loop_start=4.0, loop_end=12.0, loop_enabled=True, accent_color="#c04080")
        project.rows[0].clips.append(clip)
        project.rows[0].solo = True
        project.rows[0].color = "#7f9dff"

        loaded = Project.from_dict(project.to_dict())
        restored = loaded.rows[0].clips[0]
        self.assertEqual((restored.offset, restored.source_length), (0.25, 0.5))
        self.assertTrue(restored.loop)
        self.assertTrue(restored.reverse)
        self.assertTrue(restored.mute)
        self.assertEqual(restored.track, 6)
        self.assertEqual((loaded.loop_start, loaded.loop_end), (4.0, 12.0))
        self.assertTrue(loaded.loop_enabled)
        self.assertEqual(loaded.accent_color, "#c04080")
        self.assertTrue(loaded.rows[0].solo)
        self.assertEqual(loaded.rows[0].color, "#7f9dff")

    def test_looped_block_repeats_trim_for_arranged_length(self):
        clip = Clip(
            kind="audio", ref="wav", length_beats=2.0, offset=0.25, source_length=0.5, loop=True
        )
        self.engine._spawn_audio_clip(clip, 0)

        voice = self.engine.voices[-1]
        self.assertEqual((voice.s0, voice.s1), (5, 15))
        self.assertEqual(voice.length, 40)
        self.assertTrue(voice.loop)

    def test_reverse_keeps_the_same_source_trim(self):
        clip = Clip(kind="audio", ref="wav", offset=0.25, source_length=0.5, reverse=True)
        data, s0, s1 = self.engine._audio_clip_source(clip)

        self.assertIs(data, self.library.reversed_audio("wav"))
        self.assertEqual((s0, s1), (25, 35))
        # Original frames 5..14 become 14..5 after the range is reversed.
        self.assertEqual(data[s0, 0], 14)
        self.assertEqual(data[s1 - 1, 0], 5)

    def callback(self):
        out = np.zeros((16, 2), dtype=np.float32)
        self.engine._callback(out, 16, None, False)
        return out

    def test_song_loop_can_extend_past_the_last_clip(self):
        self.engine.project.rows[0].clips.append(Clip(kind="audio", ref="wav", length_beats=1.0))
        self.engine.project.loop_start = 0.0
        self.engine.project.loop_end = 4.0
        self.engine.mode = "song"
        self.engine.loop_song = True
        self.engine.playing = True
        self.engine.beat = 0.9

        self.callback()
        self.assertTrue(self.engine.playing)
        self.assertAlmostEqual(self.engine.beat, 1.7)

    def test_transport_stop_releases_playlist_audio(self):
        clip = Clip(kind="audio", ref="wav", length_beats=8.0, loop=True)
        self.engine._spawn_audio_clip(clip, 0)
        self.engine.stop_transport(False)
        self.callback()
        live = [v for v in self.engine.voices if v.pad_index == -1 and not v.dead]
        self.assertEqual(live, [])

    def test_resume_inside_audio_block_starts_at_matching_position(self):
        clip = Clip(kind="audio", ref="wav", length_beats=2.0)
        self.engine.project.rows[0].clips.append(clip)
        self.engine.mode = "song"
        self.engine.beat = 1.0
        self.engine.play()

        self.callback()
        live = [v for v in self.engine.voices if v.pad_index == -1 and not v.dead]
        self.assertEqual(len(live), 1)
        self.assertGreaterEqual(live[0].age, 20)

    def test_muted_clip_and_unsoloed_row_do_not_schedule(self):
        muted = Clip(kind="audio", ref="wav", length_beats=2.0, mute=True)
        audible = Clip(kind="audio", ref="wav", start_beat=2.0, length_beats=2.0)
        self.engine.project.rows[0].clips.append(muted)
        self.engine.project.rows[1].clips.append(audible)
        self.engine.project.rows[0].solo = True
        self.engine.mode = "song"
        notes, audio = self.engine._collect(0.0, 4.0)
        self.assertEqual(notes, [])
        self.assertEqual(audio, [])


if __name__ == "__main__":
    unittest.main()
