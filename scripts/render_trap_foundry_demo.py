#!/usr/bin/env python3
"""Create a playable eight-bar pack demo and render it through Studio's engine."""

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpclab.production_runtime import configure

configure()

import soundfile as sf
from mpclab.engine import Engine
from mpclab.factory import install_kit, groove
from mpclab.library import Library
from mpclab.model import Project, Pad, Row, Clip
from mpclab.music import Note


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new output directory for WAV and project")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="trap-foundry-demo-") as folder:
        library = Library(Path(folder), include_bundled=True)
        project = Project(name="Trap Foundry - Night Shift", bpm=140)
        for index, clip in enumerate(install_kit(library, 3)):
            project.pads[index] = Pad(
                sample_id=clip.id,
                name=clip.name,
                end=clip.duration,
                gain=0.6 if index in (0, 1) else 0.4,
                track=index,
                choke=1 if index in (2, 4) else 0,
            )
        bass = next(c for c in library.clips.values() if c.name.startswith("AH_808_03"))
        project.pads[8] = Pad(
            sample_id=bass.id,
            name=bass.name,
            root_note=bass.root_note,
            gain=0.55,
            mode="gate",
            mono=True,
            choke=2,
            track=6,
        )
        pattern = groove(3, 0)
        pattern.notes = [
            Note(pitch, beat, duration, velocity, pad=8)
            for pitch, beat, duration, velocity in (
                (24, 0, 1.25, 0.9),
                (24, 1.75, 0.5, 0.75),
                (31, 3.5, 0.4, 0.8),
                (27, 4, 1.25, 0.9),
                (24, 5.5, 0.75, 0.8),
                (22, 7, 0.75, 0.85),
            )
        ]
        project.patterns = [pattern]
        project.current_pattern = pattern.id
        project.rows = [
            Row(
                name="Trap Foundry",
                clips=[
                    Clip(kind="pattern", ref=pattern.id, start_beat=beat, length_beats=8)
                    for beat in (0, 8, 16, 24)
                ],
            )
        ]
        project.song_length_beats = 32
        project.loop_start, project.loop_end = 0, 32
        project.master = 0.8
        project.save(args.output / "Night Shift.json")
        engine = Engine(library)
        engine.project = project
        with sf.SoundFile(
            args.output / "Night Shift.wav",
            mode="w",
            samplerate=48000,
            channels=2,
            subtype="PCM_24",
        ) as output:
            for block in engine.iter_offline_blocks(mode="song", tail=1):
                output.write(block)
        engine.stop()
        print(f"Saved editable project and 24-bit WAV in {args.output}")


if __name__ == "__main__":
    main()
