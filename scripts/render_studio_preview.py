#!/usr/bin/env python
"""Render the actual Qt workstation offscreen with a disposable demo library.

No desktop windows, device streams, user settings or real projects are touched.
"""

import os
import argparse
from pathlib import Path
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtWidgets import QApplication
from mpclab.engine import Engine
from mpclab.model import Project, Clip, Row, Pattern
from mpclab.music import Note
from mpclab.ui import main_window, theme


class PreviewSettings:
    IniFormat = UserScope = None

    def __init__(self, *args):
        self.values = {"audio/setup_complete": True}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def populate(window):
    project = Project(name="Midnight in the crates", bpm=92)
    pattern = project.pattern()
    pattern.bars = 2
    pattern.name = "Dust & swing"
    names = (
        "Kick · Tape punch",
        "Snare · Warm room",
        "Closed hat · Dust",
        "Clap · Vinyl snap",
        "Percussion · Wood",
        "Open hat · Air",
        "Kick · Sub weight",
        "Snare · Rim ghost",
    )
    rng = np.random.default_rng(42)
    for i, name in enumerate(names):
        t = np.arange(18000) / 48000
        wave = (
            (np.sin(2 * np.pi * (55 + 25 * i) * t) if i in (0, 6) else rng.uniform(-1, 1, len(t)))
            * np.exp(-t * (12 + i * 3))
            * 0.4
        )
        data = np.column_stack((wave, wave)).astype(np.float32)
        clip = window.library.add_audio(data, name)
        pad = project.pads[i]
        pad.sample_id, pad.name, pad.end, pad.track = clip.id, name, clip.duration, i
        pattern.steps[i] = {
            step: (0.9 if step % 4 == 0 else 0.55)
            for step in range((0, 4, 2, 4, 3, 6, 8, 12)[i], 32, (8, 8, 2, 8, 4, 8, 16, 16)[i])
        }
        project.tracks[i].name = (
            "Kick",
            "Snare",
            "Hats",
            "Clap",
            "Perc",
            "Open hat",
            "Sub",
            "Ghost",
        )[i]
    for name in (
        "Piano · Felt room",
        "Bass · Hollow body",
        "Vocal chop · Velvet",
        "Vinyl texture · Night",
    ):
        data = rng.normal(0, 0.01, (48000, 2)).astype(np.float32)
        window.library.add_audio(data, name)
    bass = Pattern(name="Low end", bars=2)
    bass.notes = [
        Note(pitch, beat, 0.8, 0.65)
        for beat, pitch in ((0, 36), (1.5, 36), (3, 43), (4, 39), (5.5, 39), (7, 34))
    ]
    keys = Pattern(name="Amber keys", bars=2)
    keys.notes = [Note(pitch, beat, 1.8, 0.55) for beat in (0, 2, 4, 6) for pitch in (60, 63, 67)]
    lead = Pattern(name="After hours", bars=2)
    lead.notes = [Note(72 + n % 5 * 2, n * 0.5, 0.35, 0.6) for n in range(16)]
    project.patterns.extend((bass, keys, lead))
    project.rows = [
        Row(
            name=label,
            clips=[
                Clip(kind="pattern", ref=pat.id, start_beat=beat, length_beats=8) for beat in starts
            ],
        )
        for label, pat, starts in (
            ("01  Drums", pattern, (0, 8, 16, 24)),
            ("02  Bass", bass, (8, 16, 24)),
            ("03  Keys", keys, (0, 8, 16, 24)),
            ("04  Melody", lead, (8, 24)),
        )
    ]
    t = np.arange(48000 * 8) / 48000
    wave = (np.sin(2 * np.pi * 174 * t) * (0.5 + 0.5 * np.sin(t * 4)) * 0.15).astype(np.float32)
    clip = window.library.add_audio(np.column_stack((wave, wave)), "Vocal · Velvet verse")
    project.rows.append(
        Row(
            name="05  Vocals",
            clips=[Clip(kind="audio", ref=clip.id, start_beat=8, length_beats=12)],
        )
    )
    project.rows.append(
        Row(
            name="06  Texture",
            clips=[
                Clip(kind="audio", ref=clip.id, start_beat=0, length_beats=8),
                Clip(kind="audio", ref=clip.id, start_beat=24, length_beats=8),
            ],
        )
    )
    project.song_length_beats = 32
    project.loop_start, project.loop_end = 8, 24
    window._apply_project(project)
    project.slices[clip.id] = [0.0, 1.2, 2.4, 4.0, 6.0]
    window.load_clip_into_editor(clip.id)
    window.playlist.px_per_beat = 24
    window.playlist.refresh()
    window.engine.beat = 10.75
    window.browser.open_crate("drums")
    for index in range(min(2, window.browser.list.topLevelItemCount())):
        instrument = window.browser.list.topLevelItem(index)
        instrument.setExpanded(True)
        for child in range(instrument.childCount()):
            instrument.child(child).setExpanded(True)
    window.status.showMessage("Midnight in the crates · 92 BPM · Drag sounds to pads or Playlist")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent.parent / "previews"
    )
    destination = parser.parse_args().output_dir
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    main_window.QSettings = PreviewSettings
    Engine.start = lambda self, device=None: None
    with tempfile.TemporaryDirectory(prefix="anharmonic-ui-preview-") as root:
        window = main_window.MainWindow(Path(root), restore_session=False)
        window._ui_timer.stop()
        window._autosave_timer.stop()
        populate(window)
        window.resize(1680, 1040)
        window.tabs.setCurrentIndex(8)
        window.show()  # QT_QPA_PLATFORM=offscreen: no desktop window is created.
        app.processEvents()
        window.main_splitter.setSizes([304, 1096, 280])

        # Primary reference receipt: one existing Arrange editor above one live
        # existing song editor, with the real crates and right inspector still
        # wired. This is the layout the product now converges around.
        window.studio.select(2)
        app.processEvents()
        window.grab().save(str(destination / "studio-song.png"))

        window.studio.select(1)
        app.processEvents()
        window.grab().save(str(destination / "studio-crates.png"))
        window.studio.select(4)
        app.processEvents()
        window.grab().save(str(destination / "revamp-instruments.png"))
        window.studio.select(0)
        app.processEvents()
        window.grab().save(str(destination / "revamp-sampling.png"))
        window.pad_side.show()
        window.main_splitter.setSizes([270, 1110, 300])
        window.pad_inspector.set_pad(0)
        app.processEvents()
        window.grab().save(str(destination / "revamp-sampling-inspector.png"))
        window.pad_side.hide()
        window.studio.select(3)
        app.processEvents()
        window.grab().save(str(destination / "studio-console.png"))
        window.show_tab(6)
        app.processEvents()
        window.grab().save(str(destination / "studio-piano.png"))
        window.resize(960, 760)
        window.tabs.setCurrentIndex(8)
        window.main_splitter.setSizes([260, 700, 0])
        window.pad_side.hide()
        window.studio.select(2)
        app.processEvents()
        window.grab().save(str(destination / "studio-song-narrow.png"))
        window.studio.select(1)
        app.processEvents()
        window.grab().save(str(destination / "studio-narrow.png"))
        for index, name in ((4, "instruments"), (0, "sampling")):
            window.studio.select(index)
            app.processEvents()
            window.grab().save(str(destination / f"revamp-{name}-narrow.png"))
        window.studio.select(1)
        window.apply_theme("light")
        for _ in range(5):
            app.processEvents()
        window.grab().save(str(destination / "studio-light.png"))
        for index, name in ((4, "instruments"), (0, "sampling")):
            window.studio.select(index)
            app.processEvents()
            window.grab().save(str(destination / f"revamp-{name}-light.png"))
        window.resize(1680, 1040)
        window.pad_side.show()
        window.main_splitter.setSizes([270, 1110, 300])
        app.processEvents()
        window.grab().save(str(destination / "revamp-sampling-inspector-light.png"))
        window._dirty = False
        window.close()  # Only the disposable offscreen preview window.
    print(destination)
    theme.set_theme("dark")


if __name__ == "__main__":
    main()
