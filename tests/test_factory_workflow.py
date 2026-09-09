"""Playable factory sounds, nondestructive groove creation and focused navigation."""

from dataclasses import replace
import numpy as np
import pytest
from mpclab.factory import drum_sound, groove, install_kit
from mpclab.library import Library
from mpclab.synth import PATCHES, PATCH_CATEGORIES, render_patch


@pytest.mark.parametrize("kit", range(3))
def test_factory_drums_are_finite_faded_and_reused(tmp_path, kit):
    library = Library(tmp_path / "library")
    for instrument in range(8):
        audio = drum_sound(kit, instrument)
        assert np.isfinite(audio).all()
        assert np.max(np.abs(audio)) <= 0.751
        assert np.max(np.abs(audio)) > 0.1
        assert abs(audio[-1]) < 0.001
    first = install_kit(library, kit)
    assert [c.id for c in install_kit(library, kit)] == [c.id for c in first]
    pattern = groove(kit, 16)
    assert set(pattern.steps) == set(range(16, 24))
    assert all(0 <= step < pattern.total_steps for row in pattern.steps.values() for step in row)


@pytest.mark.parametrize("name", list(PATCHES)[10:])
def test_expanded_instruments_render_real_audio(name):
    patch = replace(PATCHES[name], release=0.03)
    audio = render_patch(patch, 60, 0.12, 16000)
    assert np.isfinite(audio).all()
    assert float(np.max(np.abs(audio))) > 0.0001
    assert name in PATCH_CATEGORIES


def test_new_factory_beat_preserves_existing_pads_and_undo(tmp_path, monkeypatch):
    from mpclab.engine import Engine
    from mpclab.ui import main_window
    from scripts.render_studio_preview import PreviewSettings

    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = main_window.MainWindow(tmp_path, restore_session=False)
    try:
        original = window.library.add_audio(np.zeros(480, dtype=np.float32), "User kick")
        window.project.pads[0].sample_id = original.id
        old_count = len(window.project.patterns)
        window.create_factory_beat()
        assert window.project.pads[0].sample_id == original.id
        assert len(window.project.patterns) == old_count + 1
        assert all(not p.empty for p in window.project.pads[8:16])
        assert window.project.pattern().steps
        window.studio.select(4)
        assert window.studio.stack.currentWidget() is window.studio.docks[4]
        window.synth_panel.category.setCurrentText("Bass")
        assert window.synth_panel.preset.count() >= 4
        window.synth_panel.load_preset("Clean Sub")
        assert window.project.synth.name == "Clean Sub"
        calls = []
        monkeypatch.setattr(
            window.engine, "synth_note_on", lambda note, velocity: calls.append(note)
        )
        before_notes = list(window.project.pattern().notes)
        window.synth_panel.preview_sound()
        assert calls == [window.synth_panel.base_note]
        assert window.project.pattern().notes == before_notes
        window.undo()  # preset change
        window.undo()  # factory beat
        assert len(window.project.patterns) == old_count
        assert window.project.pads[0].sample_id == original.id
        assert all(p.empty for p in window.project.pads[8:16])
    finally:
        window._dirty = False
        window.close()


def test_instrument_search_composes_with_category_without_changing_music(tmp_path, monkeypatch):
    from copy import deepcopy
    from PySide6.QtCore import Qt
    from mpclab.engine import Engine
    from mpclab.ui import main_window
    from scripts.render_studio_preview import PreviewSettings

    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = main_window.MainWindow(tmp_path, restore_session=False)
    try:
        panel = window.synth_panel
        original = deepcopy(window.project)
        history = list(window._undo)
        panel.sound_search.setText("  CLEAN   sub ")
        assert panel.sound_cards.count() == 1
        assert panel.sound_cards.item(0).data(Qt.UserRole) == "Clean Sub"
        panel.category.setCurrentText("Keys & plucks")
        assert panel.sound_cards.count() == 0
        assert "No matches" in panel.sound_result_count.text()
        panel.category.setCurrentText("Bass")
        assert panel.sound_cards.count() == 1
        panel.sound_search.clear()
        assert panel.sound_cards.count() >= 4
        assert all(
            PATCH_CATEGORIES[panel.sound_cards.item(i).data(Qt.UserRole)] == "Bass"
            for i in range(panel.sound_cards.count())
        )
        assert window.project == original
        assert window._undo == history
    finally:
        window._dirty = False
        window.close()


@pytest.mark.parametrize("browse_results", [False, True])
def test_instrument_enter_loads_and_previews_without_recording_and_undo_restores_route(
    tmp_path, monkeypatch, browse_results
):
    from copy import deepcopy
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from mpclab.engine import Engine
    from mpclab.ui import main_window
    from scripts.render_studio_preview import PreviewSettings

    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = main_window.MainWindow(tmp_path, restore_session=False)
    try:
        window.project.synth.track = 5
        before = deepcopy(window.project.synth)
        before_notes = deepcopy(window.project.pattern().notes)
        panel = window.synth_panel
        window.studio.select(4)
        window.show()
        QApplication.processEvents()
        panel.sound_search.setText("Clean Sub")
        panel.sound_search.setFocus()
        if browse_results:
            QTest.keyClick(panel.sound_search, Qt.Key_Down)
            assert QApplication.focusWidget() is panel.sound_cards
        target = panel.sound_cards if browse_results else panel.sound_search
        calls = []
        monkeypatch.setattr(
            window.engine, "synth_note_on", lambda note, velocity: calls.append((note, velocity))
        )
        QTest.keyClick(target, Qt.Key_Return)
        assert window.project.synth.name == "Clean Sub"
        assert window.project.synth.track == 5
        assert calls == [(panel.base_note, 0.7)]
        assert window.project.pattern().notes == before_notes
        assert len(window._undo) == 1
        QTest.keyClick(target, Qt.Key_Return)
        assert len(window._undo) == 1  # Previewing the loaded patch is not an edit.
        window.undo()
        assert window.project.synth == before
        panel.sound_search.setText("no such instrument")
        panel.sound_search.setFocus()
        QTest.keyClick(panel.sound_search, Qt.Key_Down)
        QTest.keyClick(panel.sound_search, Qt.Key_Return)
        assert window.project.synth == before
        assert len(calls) == 2
    finally:
        window._dirty = False
        window.close()
