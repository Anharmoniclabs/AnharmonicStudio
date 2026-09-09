#!/usr/bin/env python
"""Render real sample-workflow widgets using synthetic audio; no audio device."""

import argparse
import os
from pathlib import Path
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PySide6.QtWidgets import QApplication

from mpclab.engine import Engine
from mpclab.music import Note
from mpclab.ui import main_window
from scripts.render_studio_preview import PreviewSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/previews/sample-workflow"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    main_window.QSettings = PreviewSettings
    Engine.start = lambda self: None
    with tempfile.TemporaryDirectory(prefix="sample-ui-") as folder:
        window = main_window.MainWindow(Path(folder), restore_session=False)
        window._ui_timer.stop()
        window._autosave_timer.stop()
        for frequency, name, destination in (
            (110, "808 · Round sub", "notes"),
            (440, "Keys · Soft pluck", "notes"),
            (60, "Kick · Deep pulse", "beats"),
        ):
            t = np.arange(48000) / 48000
            audio = (0.3 * np.sin(2 * np.pi * frequency * t) * np.exp(-3 * t)).astype(np.float32)
            clip = window.library.add_audio(np.column_stack((audio, audio)), name)
            window.sample_workflow.send(clip.id, destination=destination)
        window.project.name = "Sample to song"
        window.proj_name.setText(window.project.name)
        window.project.pattern().name = "Bass & keys"
        window.project.pads[0].root_note = 45
        window.project.pads[0].track = 1
        window.project.pads[1].root_note = 69
        window.project.pads[1].track = 2
        window.project.pattern().notes = [
            Note(pitch, start, duration, 0.75, 0)
            for pitch, start, duration in (
                (45, 0, 0.75),
                (45, 1, 0.5),
                (52, 2, 0.75),
                (48, 3, 1),
                (45, 4.5, 0.75),
                (43, 6, 1.5),
            )
        ]
        window.project.pattern().steps = {2: {0: 1.0, 8: 0.8, 16: 1.0, 24: 0.85}}
        window._sync_pattern_controls()
        window.step_grid.refresh()
        window.browser.refresh()
        window.browser.open_crate("all")
        window.show_tab(8)
        window.studio.select(6)
        window.piano_roll.select_channel(0)
        for name, width, page, light in (
            ("notes-dark", 1440, 6, False),
            ("notes-laptop", 1000, 6, False),
            ("notes-light", 1440, 6, True),
            ("beats-dark", 1440, 1, False),
        ):
            window.apply_theme("light" if light else "dark")
            window.resize(width, 900)
            window.studio.select(page)
            window.show()
            for _ in range(4):
                app.processEvents()
            window.piano_roll.fit_pattern()
            app.processEvents()
            window.grab().save(str(args.output / f"{name}.png"))
        window._dirty = False
        window.close()
    print(f"Rendered real widgets to {args.output}")


if __name__ == "__main__":
    main()
