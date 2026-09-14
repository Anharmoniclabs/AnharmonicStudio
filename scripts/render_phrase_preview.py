#!/usr/bin/env python
"""Inspect real Crates and phrase controls with isolated settings/media, offscreen."""

import os
from pathlib import Path
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from mpclab.engine import Engine
from mpclab.ui import main_window
from scripts.render_studio_preview import PreviewSettings, populate


def main():
    destination = ROOT / "docs/agent/evidence/2026-09-05-four-bar-sampling"
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    main_window.QSettings = PreviewSettings
    Engine.start = lambda self, device=None: None
    with tempfile.TemporaryDirectory(prefix="anharmonic-phrase-preview-") as root:
        window = main_window.MainWindow(Path(root), restore_session=False)
        window._ui_timer.stop()
        window._autosave_timer.stop()
        populate(window)
        window.tabs.setCurrentIndex(8)
        window.studio.select(0)
        window.wave.set_selection(0, 8)
        for mode in ("dark", "light"):
            window.apply_theme(mode)
            for width in (1440, 1000):
                window.resize(width, 900)
                window.show()  # Offscreen only; no desktop window.
                app.processEvents()
                window.main_splitter.setSizes([304 if width == 1440 else 268, width - 304, 0])
                app.processEvents()
                window.grab().save(str(destination / f"sampling-{mode}-{width}.png"))
                window.browser.grab().save(str(destination / f"crates-{mode}-{width}.png"))
        window.apply_theme("dark")
        window.arrange_four_bar_phrase()
        window.resize(1440, 900)
        app.processEvents()
        window.grab().save(str(destination / "arranged-phrase.png"))
        window._dirty = False
        window.close()
    print(destination)


if __name__ == "__main__":
    main()
