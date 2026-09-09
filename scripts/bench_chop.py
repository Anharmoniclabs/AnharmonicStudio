"""Offscreen chop interaction benchmark; no real projects or audio devices touched."""

import os
import sys
import tempfile
import time
import json
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from PySide6.QtWidgets import QApplication
from mpclab.ui import main_window
from mpclab.engine import Engine
from mpclab.library import Clip
from scripts.render_studio_preview import PreviewSettings


def main():
    app = QApplication([])
    main_window.QSettings = PreviewSettings
    Engine.start = lambda self, device=None: None
    with tempfile.TemporaryDirectory(prefix="chop-bench-") as root:
        w = main_window.MainWindow(Path(root), restore_session=False)
        w._ui_timer.stop()
        w._autosave_timer.stop()
        audio = np.full((48000 * 240, 2), 0.2, dtype=np.float32)
        w.library.clips["bench"] = Clip(id="bench", name="Benchmark record", duration=240)
        w.library._audio["bench"] = audio
        w.current_clip = "bench"
        w.wave.set_clip(
            audio, w.library.peaks("bench"), 240, list(np.linspace(0, 239, 256)), clip_id="bench"
        )
        w._rebuild_chips()
        w.resize(1680, 1040)
        w.show_tab(0)
        w.show()
        app.processEvents()
        timings = []
        for i in range(12):
            t = time.perf_counter()
            w._slice_selected(i)
            app.processEvents()
            timings.append((time.perf_counter() - t) * 1000)
        w.engine._process_commands()
        paint = []
        for i in range(12):
            w.wave.set_playhead(i + 1)
            t = time.perf_counter()
            w.wave.repaint()
            paint.append((time.perf_counter() - t) * 1000)
        w.engine.voices.clear()
        for i in range(1000):
            w.engine.audition("bench", i / 10, i / 10 + 0.1)
        t = time.perf_counter()
        w.engine._process_commands()
        scrub = (time.perf_counter() - t) * 1000
        print(
            json.dumps(
                {
                    "slice_click_ms": {"p50": float(np.median(timings)), "max": max(timings)},
                    "wave_repaint_ms": {"p50": float(np.median(paint)), "max": max(paint)},
                    "1000_scrub_commands_ms": scrub,
                    "preview_voices": len(w.engine.voices),
                }
            )
        )
        w._dirty = False
        w.close()


if __name__ == "__main__":
    main()
