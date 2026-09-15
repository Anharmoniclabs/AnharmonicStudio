"""Playlist audio blocks: persistent trim/loop state and engine voices."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtGui import QImage, QPainter

from mpclab.engine import Engine
from mpclab.model import Clip, Project
from mpclab.ui import playlist_rendering as playlist_module
from mpclab.ui.playlist import PlaylistView, audio_stretch_label


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

    def test_audio_block_stretches_its_trim_to_the_arranged_length(self):
        clip = Clip(kind="audio", ref="wav", length_beats=4.0, offset=0.25, source_length=0.5)
        self.engine._spawn_audio_clip(clip, 0)

        voice = self.engine.voices[-1]
        # Ten source frames are stretched over four seconds (80 output frames)
        # without mutating the trim range.
        self.assertEqual((voice.s0, voice.s1, voice.length), (5, 15, 80))
        self.assertEqual(voice.rate, 0.125)

    def test_stretch_badge_describes_arranged_time_without_affecting_loops(self):
        # At 120 BPM, one second naturally occupies two beats.
        stretched = Clip(kind="audio", ref="wav", length_beats=8.0, source_length=1.0)
        compressed = Clip(kind="audio", ref="wav", length_beats=1.0, source_length=1.0)
        natural = Clip(kind="audio", ref="wav", length_beats=2.0, source_length=1.0)
        looped = Clip(kind="audio", ref="wav", length_beats=8.0, source_length=1.0, loop=True)

        self.assertEqual(audio_stretch_label(stretched, 120.0), "STRETCH ×4")
        self.assertEqual(audio_stretch_label(compressed, 120.0), "COMPRESS ×0.5")
        self.assertIsNone(audio_stretch_label(natural, 120.0))
        self.assertIsNone(audio_stretch_label(looped, 120.0))

    def test_non_loop_waveform_always_draws_the_complete_source_selection(self):
        """Compressed and stretched clips display the same selected source."""

        meta = SimpleNamespace(name="Take", duration=1.0)
        library = SimpleNamespace(
            peaks=lambda _ref: np.zeros((16, 2), dtype=np.float32),
            clips={"wav": meta},
        )
        app = SimpleNamespace(project=Project(bpm=120.0), library=library)
        view = SimpleNamespace(
            app=app,
            selected_clips=[],
            _hover_clip=None,
            px_per_beat=26.0,
            beat_to_x=lambda beat: beat * 26.0,
        )
        image = QImage(400, 80, QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            with patch.object(playlist_module, "draw_peaks") as draw:
                for length_beats in (0.5, 8.0):  # compressed, then stretched
                    PlaylistView._draw_clip(
                        view,
                        painter,
                        Clip(
                            kind="audio",
                            ref="wav",
                            length_beats=length_beats,
                            offset=0.25,
                            source_length=0.5,
                        ),
                        3,
                        40,
                        False,
                    )
                self.assertEqual(draw.call_count, 2)
                for call in draw.call_args_list:
                    self.assertEqual(call.args[3:5], (0.25, 0.75))
        finally:
            painter.end()

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
