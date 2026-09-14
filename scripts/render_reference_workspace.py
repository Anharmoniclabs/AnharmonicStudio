#!/usr/bin/env python
"""Render isolated, device-free receipts of the user's workspace direction."""

import os
from pathlib import Path
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from mpclab.engine import Engine
from mpclab.ui import main_window
from scripts.render_studio_preview import PreviewSettings, populate


def main():
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/anharmonic-workspace")
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    main_window.QSettings = PreviewSettings
    Engine.start = lambda self: None
    with tempfile.TemporaryDirectory(prefix="studio-receipt-") as root:
        window = main_window.MainWindow(Path(root), restore_session=False)
        window._ui_timer.stop()
        window._autosave_timer.stop()
        populate(window)
        window.show()  # Offscreen platform only; never a desktop window.
        for mode, color, width in (
            ("dark", "#d6ab65", 1680),
            ("dark", "#b88aff", 1440),
            ("light", "#168575", 1440),
            ("dark", "#b88aff", 960),
        ):
            window.project.accent_color = color
            window.apply_theme(mode)
            window.resize(width, 940 if width > 1000 else 760)
            window.studio.select(2)
            window.browser_frame.setVisible(width > 1000)
            window.pad_side.setVisible(width > 1000)
            if hasattr(window, "track_inspector"):
                window.track_inspector.select_row(window.project.rows[4])
            window.main_splitter.setSizes([280, width - 580, 290])
            for _ in range(4):
                app.processEvents()
            window.grab().save(str(destination / f"song-{mode}-{width}.png"))
        window.resize(1440, 940)
        window.browser_frame.show()
        window.pad_side.hide()
        for index, name in ((0, "sampler"), (1, "beats"), (6, "notes"), (3, "mix")):
            if index == 6:
                window.project.current_pattern = window.project.patterns[2].id
                window._sync_pattern_controls()
            window.show_tab(index)
            app.processEvents()
            window.grab().save(str(destination / f"{name}-dark-1440.png"))
        window.studio.select(2)
        window.set_playlist_focus(True)
        app.processEvents()
        window.grab().save(str(destination / "song-focus-1440.png"))
        window._dirty = False
        window.close()  # Only this disposable, device-free fixture.
    print(destination)


if __name__ == "__main__":
    main()
