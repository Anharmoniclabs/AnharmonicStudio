"""The beginner trap starter creates a musical, editable project."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mpclab.starter import (
    TRAP_PAD_ROLES,
    find_stem_family,
    load_trap_manifest,
    make_stem_remix_project,
    make_trap_project,
    project_has_music,
)


class TrapStarterTests(unittest.TestCase):
    def setUp(self):
        self.kit = {role: f"clip-{n}" for n, role in enumerate(TRAP_PAD_ROLES)}

    def test_starter_maps_core_kit_and_writes_a_beat(self):
        project = make_trap_project(self.kit)
        self.assertEqual(project.bpm, 142.0)
        self.assertEqual(project.pattern().div, 8)
        self.assertEqual(project.pads[0].name, "808 SUB")
        self.assertEqual(project.pads[0].track, 1)
        self.assertEqual(project.pads[4].choke, 1)
        self.assertTrue(project.pattern().steps)
        self.assertTrue(project_has_music(project))

    def test_snare_lands_on_half_time_backbeat(self):
        project = make_trap_project(self.kit)
        self.assertEqual(set(project.pattern().steps[2]), {16, 48})

    def test_missing_core_sound_is_rejected(self):
        del self.kit["snare"]
        with self.assertRaisesRegex(ValueError, "snare"):
            make_trap_project(self.kit)

    def test_absent_manifest_is_an_empty_kit(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_trap_manifest(Path(folder)), ({}, {}))

    def test_stem_remix_maps_slices_and_builds_a_song(self):
        family = {
            role: SimpleNamespace(
                id=f"clip-{role}",
                name=f"Song - {role}",
                stem=role,
                parent="source",
                duration=120.0,
                bpm=144.0,
                created=10.0,
            )
            for role in ("drums", "other", "bass", "vocals")
        }
        project = make_stem_remix_project(family)

        self.assertEqual(project.bpm, 144.0)
        self.assertEqual([pattern.name[0] for pattern in project.patterns], ["I", "A", "B", "B"])
        self.assertEqual(project.current_pattern, project.patterns[1].id)
        self.assertEqual(project.pads[0].sample_id, "clip-drums")
        self.assertEqual(project.pads[8].track, 2)
        self.assertTrue(project.pads[7].reverse)
        self.assertEqual(len(project.rows[0].clips), 6)
        self.assertEqual(project.song_end(), 128.0)
        self.assertEqual(project.loop_end, 128.0)

    def test_selected_stem_family_wins_over_newer_family(self):
        def stem(parent, role, created):
            return SimpleNamespace(id=f"{parent}-{role}", parent=parent, stem=role, created=created)

        clips = {}
        for parent, created in (("wanted", 1.0), ("newest", 9.0)):
            for role in ("drums", "other"):
                clip = stem(parent, role, created)
                clips[clip.id] = clip
        clips["wanted"] = SimpleNamespace(id="wanted", parent=None, stem=None, created=0.0)

        family = find_stem_family(clips, "wanted-other")
        self.assertEqual(family["drums"].parent, "wanted")
        family = find_stem_family(clips, "wanted")
        self.assertEqual(family["other"].parent, "wanted")

    def test_stem_remix_requires_drums(self):
        family = {
            "other": SimpleNamespace(
                id="music",
                name="Song - other",
                stem="other",
                parent="source",
                duration=10.0,
                bpm=100.0,
                created=1.0,
            )
        }
        with self.assertRaisesRegex(ValueError, "drums"):
            make_stem_remix_project(family)


if __name__ == "__main__":
    unittest.main()
