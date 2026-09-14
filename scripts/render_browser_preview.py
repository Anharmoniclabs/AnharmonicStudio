#!/usr/bin/env python
"""Inspect the real sample index read-only and render an offscreen browser."""

import os
from pathlib import Path
import sys
from collections import Counter
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from mpclab.library import Library
from mpclab.crates import sample_group
from mpclab.ui.browser import BrowserPanel
from mpclab.ui import theme


def main():
    # scan() only reads metadata and linked pack headers; no audio loading,
    # journal initialization, project restoration, or settings writes.
    library = Library.__new__(Library)
    library.root = ROOT / "library"
    library.clips = {}
    library.scan()
    counts = Counter(sample_group(clip) for clip in library.clips.values())
    packs = Counter(sample_group(clip) for clip in library.clips.values() if clip.kind == "pack")
    print(
        {
            "total": len(library.clips),
            "pack_sounds": sum(packs.values()),
            "pack_groups": {" / ".join(key): count for key, count in sorted(packs.items())},
            "all_groups": {" / ".join(key): count for key, count in sorted(counts.items())},
        }
    )
    app = QApplication([])
    app.setStyleSheet(theme.stylesheet())
    panel = BrowserPanel(SimpleNamespace(library=library, separator=SimpleNamespace(jobs={})))
    panel._timer.stop()
    panel.resize(304, 850)
    panel.open_crate("drums")
    # Reveal percussion to make the previously miscategorized pack folders visible.
    for item in panel.list.items():
        if item.text(0).startswith("Percussion"):
            item.setExpanded(True)
            item.child(0).setExpanded(True)
    panel.show()  # Offscreen only; never interacts with the desktop or chat.
    app.processEvents()
    destination = ROOT / "docs/previews/browser-organized.png"
    panel.grab().save(str(destination))
    print(f"Browser list: {panel.list.height()} / {panel.height()} px; preview: {destination}")
    panel.close()  # Our disposable offscreen widget only.


if __name__ == "__main__":
    main()
