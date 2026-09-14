#!/usr/bin/env python
"""Offscreen orchestra UI, an audible comparison reel and device-free callback evidence."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication
from mpclab import orchestra
from mpclab.engine import Engine
from mpclab.library import Library
from mpclab.model import Project
from mpclab.synth import patch_copy, render_patch
from mpclab.ui import main_window
from scripts.render_studio_preview import PreviewSettings


def main():
    destination = ROOT / "docs/agent/evidence/2026-09-05-orchestra"
    destination.mkdir(parents=True, exist_ok=True)
    reels = [
        ("Solar Strings", (60, 67)),
        ("Chamber Violins", (60, 67)),
        ("Spiccato Strings", (69,)),
        ("Pizzicato Cellos", (48,)),
        ("Golden French Horn", (60,)),
        ("Breathing Flute", (76,)),
        ("Concert Harp", (60, 67)),
        ("Rosewood Marimba", (60, 67)),
        ("Nocturne Strings", (60, 67)),
        ("Cathedral Glass", (60, 67)),
        ("Ghost Conservatory", (69,)),
        ("Silk Pulse", (60, 67)),
        ("Clockwork Garden", (69,)),
    ]
    pieces, cues = [], []
    elapsed = 0.0
    for name, notes in reels:
        patch = patch_copy(name)
        signals = [render_patch(patch, note, 1.8, 48000) for note in notes]
        signal = sum(signals) / max(1, len(signals) ** 0.5)
        signal = signal[: 48000 * 4].copy()
        signal[-480:] *= np.linspace(1, 0, 480)[:, None]
        peak = float(np.max(np.abs(signal)))
        if peak > 0.9:
            signal *= 0.9 / peak
        sf.write(
            destination / f"{name.lower().replace(' ', '-')}.wav", signal, 48000, subtype="PCM_16"
        )
        cues.append({"seconds": round(elapsed, 3), "instrument": name, "notes": notes})
        gap = np.zeros((24000, 2), dtype=np.float32)
        pieces.extend((signal, gap))
        elapsed += (len(signal) + len(gap)) / 48000
    sf.write(
        destination / "orchestral-palette-demo.wav", np.concatenate(pieces), 48000, subtype="PCM_16"
    )
    (destination / "demo-cues.json").write_text(json.dumps(cues, indent=2) + "\n")

    app = QApplication([])
    main_window.QSettings = PreviewSettings
    Engine.start = lambda self, device=None: None
    with tempfile.TemporaryDirectory(prefix="anharmonic-orchestra-preview-") as temporary:
        root = Path(temporary)
        window = main_window.MainWindow(root, restore_session=False)
        window._ui_timer.stop()
        window._autosave_timer.stop()
        window.tabs.setCurrentIndex(8)
        window.studio.select(4)
        for mode in ("dark", "light"):
            window.apply_theme(mode)
            for width in (1440, 1000):
                window.resize(width, 900)
                window.show()  # Offscreen only; never opens a desktop window.
                panel = window.synth_panel
                orchestra.prepare_patch(patch_copy("Nocturne Strings"))
                panel.load_preset("Nocturne Strings")
                panel.category.setCurrentText("Cinematic hybrids")
                app.processEvents()
                window.grab().save(str(destination / f"hybrids-{mode}-{width}.png"))
                panel.category.setCurrentText("Orchestral strings")
                panel.load_preset("Chamber Violins")
                app.processEvents()
                window.grab().save(str(destination / f"orchestra-{mode}-{width}.png"))
        window._dirty = False
        window.close()

        measurements = []
        for name in ("Chamber Violins", "Silk Pulse"):
            engine = Engine(Library(root / "benchmark-library"), blocksize=512)
            engine.project = Project()
            engine.project.synth = patch_copy(name)
            engine.preload_project_audio()
            for note in (55, 60, 64, 67, 69, 72, 76, 79):
                engine.synth_note_on(note, 0.7)
            block = np.zeros((512, 2), dtype=np.float32)
            timings = []
            for _ in range(750):
                start = time.perf_counter()
                engine._callback(block, 512, None, False)
                timings.append((time.perf_counter() - start) * 1000)
                assert np.isfinite(block).all()
            measurements.append(
                {
                    "patch": name,
                    "sample_rate": 48000,
                    "block_frames": 512,
                    "voices": 8,
                    "audio_seconds": 750 * 512 / 48000,
                    "cold_ms": timings[0],
                    "p99_ms": float(np.percentile(timings[1:], 99)),
                    "worst_ms": max(timings[1:]),
                    "deadline_ms": 512 / 48,
                    "deadline_misses": sum(t > 512 / 48 for t in timings[1:]),
                }
            )
        (destination / "callback-benchmark.json").write_text(
            json.dumps(measurements, indent=2) + "\n"
        )
        print(json.dumps(measurements, indent=2))
    print(destination)


if __name__ == "__main__":
    main()
