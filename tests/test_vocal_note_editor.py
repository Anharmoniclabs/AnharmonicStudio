"""Exercise note edits through the production command router and project undo."""

import time
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_consolidated_workflows import window  # noqa: F401


def wait_until(predicate):
    for _ in range(300):
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Vocal analysis did not complete")


def test_production_note_drag_split_bypass_and_undo(window):  # noqa: F811
    panel = window.vocal_panel
    data = (0.15 * np.sin(2 * np.pi * 225 * np.arange(96000) / 48000)).astype(np.float32)
    clip = window.library.add_audio(np.column_stack((data, data)), "Dry", kind="vocal")
    panel.refresh_takes(select=clip.id)
    panel.tune_backend.setCurrentIndex(panel.tune_backend.findData("v2"))
    panel.show()
    editor = panel.pitch_view
    editor.resize(900, 500)
    editor.analyze()
    wait_until(lambda: editor.analysis is not None)
    notes = editor.notes()
    assert notes
    first = notes[0]
    editor.selected = {0}
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key_Up)
    assert window.project.vocal.pitch_edits[clip.id][0]["target"] == first["target"] + 1
    QTest.keyClick(editor, Qt.Key_S)
    assert len(window.project.vocal.pitch_edits[clip.id]) == len(notes) + 1
    QTest.keyClick(editor, Qt.Key_B)
    assert window.project.vocal.pitch_edits[clip.id][0]["bypass"]
    restored = type(window.project).from_dict(window.project.to_dict())
    assert restored.vocal.pitch_edits == window.project.vocal.pitch_edits
    window.undo()
    assert not window.project.vocal.pitch_edits[clip.id][0]["bypass"]


def test_production_v2_render_saves_new_take_and_recipe(window):  # noqa: F811
    panel = window.vocal_panel
    mono = (0.1 * np.sin(2 * np.pi * 225 * np.arange(48000) / 48000)).astype(np.float32)
    clip = window.library.add_audio(np.column_stack((mono, mono)), "Dry", kind="vocal")
    panel.refresh_takes(select=clip.id)
    panel.tune_backend.setCurrentIndex(panel.tune_backend.findData("v2"))
    panel.render_take()
    wait_until(lambda: panel._tune_cancel is None)
    tuned = [c for c in window.library.clips.values() if c.kind == "vocal-tuned"]
    assert len(tuned) == 1, panel.analysis_label.text()
    assert tuned[0].parent == clip.id
    assert tuned[0].render_recipe["source_id"] == clip.id
    assert tuned[0].render_recipe["settings"]["backend"] == "v2"
    assert window.library.audio(tuned[0].id).shape == (48000, 2)
    assert clip.id in window.library.clips
