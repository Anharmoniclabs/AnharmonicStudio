"""Exercise the consolidated production entrypoint and previously unwired workflows."""

import time

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtWidgets import QApplication

from mpclab.application_features import attach_application_features, install_application_runtime
from mpclab.engine import Engine
from mpclab.model import Project
from mpclab.ui import main_window
from mpclab.ui.instruments import edit_instrument
from mpclab.ui.loudness_delivery import LoudnessDialog
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    install_application_runtime()
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    result = main_window.MainWindow(tmp_path, restore_session=False)
    result.controller = attach_application_features(result)
    result._ui_timer.stop()
    result._autosave_timer.stop()
    yield result
    result._dirty = False
    result.close()


def test_consolidated_commands_are_attached_once(window):
    commands = (
        "midi.file_import",
        "midi.file_export",
        "midi.pattern_export",
        "audio.loudness",
        "audio.normalize",
        "instruments.manage",
        "automation.copy_lane",
        "automation.paste_lane",
        "track.folder_create",
        "project.import_dawproject",
    )
    for key in commands:
        assert callable(window.controller.registry.get(key).callback)
    dialog = window.instrument_dialog
    attach_application_features(window)
    assert window.instrument_dialog is dialog
    assert window.midi_file_controller is not None


def test_instrument_lifecycle_channel_capture_undo_and_reopen(window, tmp_path):
    first = edit_instrument(window, "add", name="Bass", midi_channel=1, track=2)
    second = edit_instrument(window, "duplicate", name="Lead", midi_channel=2, track=3)
    assert first != second
    assert window.project.instrument_patch(first) is not window.project.instrument_patch(second)
    window.engine.mode = "pattern"
    window.engine.playing = window.engine.recording = True
    router = window.devices.router
    window.engine.beat = 1
    router.handle("keys", [0x91, 60, 100])
    router.handle("keys", [0x92, 60, 90])
    window.engine.beat = 2
    # Reassignment while held must not change the note-off destination.
    window.project.instruments[0].midi_channel = 4
    router.handle("keys", [0x81, 60, 0])
    router.handle("keys", [0x82, 60, 0])
    notes = window.project.pattern().notes
    assert {(n.instrument, n.channel, n.start, n.duration) for n in notes} == {
        (first, 1, 1, 1),
        (second, 2, 1, 1),
    }
    window.engine.playing = window.engine.recording = False
    path = tmp_path / "instruments.json"
    window.project.save(path)
    reopened = Project.load(path)
    assert reopened.to_dict() == window.project.to_dict()
    with pytest.raises(ValueError, match="has notes"):
        edit_instrument(window, "remove")
    third = edit_instrument(window, "duplicate", name="Unused", track=4)
    edit_instrument(window, "remove")
    assert third not in {i.id for i in window.project.instruments}
    window.undo()
    assert third in {i.id for i in window.project.instruments}
    window.redo()
    assert third not in {i.id for i in window.project.instruments}


def test_normalization_dialog_produces_verified_file_without_changing_source(window, tmp_path):
    source = tmp_path / "source.wav"
    destination = tmp_path / "delivery.wav"
    t = np.arange(48000 * 4) / 48000
    sf.write(
        source, np.column_stack([0.1 * np.sin(2 * np.pi * 1000 * t)] * 2), 48000, subtype="FLOAT"
    )
    before = source.read_bytes()
    dialog = LoudnessDialog(window, destination=destination)
    dialog.source.setText(str(source))
    dialog.start_analysis()
    deadline = time.monotonic() + 30
    while dialog.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not dialog.busy
    assert destination.is_file(), dialog.status.text()
    assert destination.with_suffix(".wav.analysis.json").is_file()
    assert "Verified" in dialog.status.text()
    assert source.read_bytes() == before
    dialog.close()


def test_production_midi_import_edit_export_survives_save_and_reopen(window, tmp_path):
    from mpclab.midi_smf import MidiEvent, MidiFile, encode_midi, parse_midi, track_notes
    from mpclab.midi_file_state import export_imported_midi, midi_sources

    source = tmp_path / "notes.mid"
    source.write_bytes(
        encode_midi(
            MidiFile(
                0,
                480,
                (
                    (
                        MidiEvent(0, 0xFF, (500000).to_bytes(3, "big"), 0x51),
                        MidiEvent(0, 0xB3, bytes([1, 32])),
                        MidiEvent(0, 0x93, bytes([60, 100])),
                        MidiEvent(480, 0x83, bytes([60, 45])),
                        MidiEvent(480, 0xFF, b"", 0x2F),
                    ),
                ),
            )
        )
    )
    before = window.project.to_dict()
    window.midi_file_controller.import_path(source)
    assert window.project.pattern().notes[0].channel == 3
    window.project.pattern().notes[0].pitch = 64
    project_path = tmp_path / "midi-project.json"
    window.project.save(project_path)
    reopened = Project.load(project_path)
    source_id = midi_sources(reopened)[0]["id"]
    exported = parse_midi(export_imported_midi(reopened, source_id))
    notes = [n for track in exported.tracks for n in track_notes(track)]
    assert [(n.pitch, n.channel, n.release_velocity) for n in notes] == [(64, 3, 45)]
    assert any(e.status == 0xB3 and e.data == bytes([1, 32]) for t in exported.tracks for e in t)
    window.undo()
    assert window.project.to_dict() == before


def test_production_shortcuts_yield_step_editor_navigation_and_toggle(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    grid = window.step_grid
    window.show()
    window.studio.select(window.TAB_SEQ)
    grid.setFocus()
    QTest.keyClick(grid, Qt.Key_End)
    QTest.keyClick(grid, Qt.Key_Home)
    assert grid._keyboard_cell[1] == 0
    QTest.keyClick(grid, Qt.Key_Space)
    assert window.project.pattern().get(0, 0) == 1.0
    assert not window.engine.playing
    QTest.keyClick(grid, Qt.Key_Delete)
    assert window.project.pattern().get(0, 0) is None
