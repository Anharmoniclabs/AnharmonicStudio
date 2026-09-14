"""Generated-sound library storage tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from mpclab.library import Library


class LibraryRenderTests(unittest.TestCase):
    def test_generated_audio_is_stored_and_immediately_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder), sample_rate=8000)
            audio = np.zeros((800, 2), dtype=np.float32)
            audio[:, 0] = 0.1
            clip = library.add_audio(audio, "Copper / Test", kind="render")
            self.assertEqual(clip.name, "Copper _ Test")
            self.assertEqual(clip.kind, "render")
            self.assertEqual(clip.duration, 0.1)
            self.assertTrue(library.wav_path(clip.id).exists())
            self.assertIs(library.audio(clip.id), library._audio[clip.id])

    def test_delete_moves_clip_files_to_recoverable_library_trash(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder), sample_rate=8000)
            clip = library.add_audio(np.zeros((80, 2), dtype=np.float32), "recover me")

            moved_to = library.delete(clip.id)

            self.assertIsNotNone(moved_to)
            self.assertTrue((moved_to / "audio.wav").exists())
            self.assertTrue((moved_to / "meta.json").exists())
            self.assertNotIn(clip.id, library.clips)
            self.assertFalse(library.folder(clip.id).exists())

    def test_registered_pack_is_indexed_in_place_with_stable_clip_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            pack = base / "pack"
            kicks = pack / "Hits" / "Kicks"
            metadata = pack / "__MACOSX" / "Hits"
            kicks.mkdir(parents=True)
            metadata.mkdir(parents=True)
            source = kicks / "Kick 01.wav"
            sf.write(source, np.full(80, 0.25, dtype=np.float32), 8000)
            sf.write(kicks / "._Kick 01.wav", np.zeros(8), 8000)
            sf.write(metadata / "Ghost.wav", np.zeros(8), 8000)

            library_root = base / "library"
            library = Library(library_root, sample_rate=16_000)
            self.assertEqual(library.register_pack(pack, "Drum Pack"), 1)
            clips = [clip for clip in library.clips.values() if clip.kind == "pack"]
            self.assertEqual(len(clips), 1)
            clip = clips[0]
            self.assertEqual(clip.category, "Hits/Kicks")
            self.assertEqual(library.wav_path(clip.id), source.resolve())
            self.assertFalse(library.folder(clip.id).exists())
            self.assertEqual(library.audio(clip.id).shape, (160, 2))

            reopened = Library(library_root, sample_rate=16_000)
            self.assertIn(clip.id, reopened.clips)
            self.assertEqual(reopened.wav_path(clip.id), source.resolve())


if __name__ == "__main__":
    unittest.main()
