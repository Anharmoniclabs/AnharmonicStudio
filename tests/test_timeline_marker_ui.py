from __future__ import annotations

from dataclasses import replace
import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from mpclab.application_features import attach_application_features, install_application_runtime
from mpclab.engine import Engine
from mpclab.model import Project
from mpclab.timeline_markers import TimelineMarker, create_marker, marker_items
from mpclab.ui import main_window, timeline_markers as ui
from scripts.render_studio_preview import PreviewSettings


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    monkeypatch.setattr(Engine, "start", lambda self: None)
    install_application_runtime()
    app_window = main_window.MainWindow(tmp_path, restore_session=False)
    app_window._ui_timer.stop()
    app_window._autosave_timer.stop()
    commands = attach_application_features(app_window)
    markers = app_window.timeline_marker_controller
    markers.timer.stop()
    yield app_window, commands, markers
    app_window.track_capture.active = False
    app_window.track_capture.pending = False
    app_window.track_capture.unsaved = None
    app_window._dirty = False
    app_window.close()
    QApplication.processEvents()


def accept_dialog(monkeypatch, **values):
    def execute(dialog):
        for key, value in values.items():
            if key == "color":
                dialog.color = value
            elif key == "name":
                dialog.name.setText(value)
            else:
                getattr(dialog, key).setValue(value)
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(ui.MarkerEditDialog, "exec", execute)


def test_production_buttons_registry_menu_and_idempotent_attachment(window, monkeypatch):
    app, commands, markers = window
    assert ui.attach_timeline_markers(app, commands) is markers
    assert app.findChild(ui.TimelineMarkerStrip, "timelineMarkerStrip") is markers.strip
    assert len([action for action in app.menuBar().actions() if action.text() == "Markers"]) == 1
    assert commands.registry.bindings["markers.add"] == "Ctrl+Alt+M"
    assert "markers.next" in {spec.id for spec in commands.registry.search("timeline")}
    app.engine.beat = 6.5
    accept_dialog(monkeypatch, name="Singer cue", color="#123456")
    QTest.mouseClick(markers.buttons["markers.cue"], Qt.LeftButton)
    item = marker_items(app.project)[0]
    assert (item.kind, item.name, item.start_beat, item.color) == (
        "cue",
        "Singer cue",
        6.5,
        "#123456",
    )
    assert app._dirty
    menu = next(action.menu() for action in app.menuBar().actions() if action.text() == "Markers")
    accept_dialog(monkeypatch, name="Intro")
    menu.actions()[0].trigger()
    assert len(marker_items(app.project)) == 2
    accept_dialog(monkeypatch, name="Keyboard marker")
    QTest.keyClick(app, Qt.Key_M, Qt.ControlModifier | Qt.AltModifier)
    assert any(item.name == "Keyboard marker" for item in marker_items(app.project))


def test_marker_edits_undo_redo_and_disk_roundtrip(window, monkeypatch, tmp_path):
    app, _, markers = window
    accept_dialog(monkeypatch, name="Verse", start=8, end=16)
    region = markers.add("region")
    assert region.kind == "region"
    markers.replace_entry(
        replace(region, name="Chorus", color="#aabbcc", start_beat=12, end_beat=20)
    )
    assert marker_items(app.project)[0].name == "Chorus"
    app.undo()
    markers.refresh()
    assert marker_items(app.project)[0] == region
    app.redo()
    markers.refresh()
    assert marker_items(app.project)[0].name == "Chorus"
    path = tmp_path / "named-regions.json"
    app.project.save(path)
    reopened = Project.load(path)
    app._apply_project(reopened)
    markers.refresh()
    assert markers.items == marker_items(reopened)
    markers.remove(markers.items[0].id)
    assert markers.items == []
    app.undo()
    markers.refresh()
    assert markers.items[0].name == "Chorus"


def test_navigation_and_region_loop_respect_capture_guard(window, monkeypatch):
    app, commands, markers = window
    first = create_marker(app.project, "marker", "First", 4)
    region = create_marker(app.project, "region", "Hook", 12, 20)
    markers.refresh()
    positions = []
    monkeypatch.setattr(app.engine, "set_position", positions.append)
    app.engine.beat = 6
    commands.registry.execute("markers.next")
    assert positions == [12]
    assert markers.selected_id == region.id
    commands.registry.execute("markers.previous")
    assert positions == [12, 4]
    markers.use_region(region.id)
    assert (app.project.loop_start, app.project.loop_end) == (12, 20)
    app.track_capture.active = True
    markers.navigate(first.id)
    assert positions == [12, 4]
    app.project.loop_start, app.project.loop_end = 0, 8
    markers.use_region(region.id)
    assert (app.project.loop_start, app.project.loop_end) == (0, 8)


def test_manager_edit_color_export_and_replacement_refresh(window, monkeypatch, tmp_path):
    app, _, markers = window
    cue = create_marker(app.project, "cue", "Old", 4)
    markers.refresh()
    markers.select(cue.id)
    markers.show_manager()
    assert markers.manager.table.rowCount() == 1
    accept_dialog(monkeypatch, name="New cue", color="#a1b2c3")
    markers.edit()
    assert markers.manager.table.item(0, 1).text() == "New cue"
    assert markers.manager.table.item(0, 4).text() == "#a1b2c3"
    path = tmp_path / "markers.json"
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *_: (str(path), "JSON (*.json)"))
    QTest.mouseClick(markers.buttons["markers.export"], Qt.LeftButton)
    exported = json.loads(path.read_text())
    assert exported["items"][0]["color"] == "#a1b2c3"
    assert exported["items"][0]["name"] == "New cue"
    app._apply_project(Project())
    markers.refresh()
    assert markers.manager.table.rowCount() == 0
    assert markers.selected_id is None


def test_snapped_drag_move_and_region_edge_resize_are_undoable(window):
    app, _, markers = window
    region = create_marker(app.project, "region", "Move", 4, 12)
    markers.refresh()
    strip = markers.strip
    strip.resize(900, 50)
    app.playlist.snap = 1
    start = QPoint(int(strip.beat_x(6)), 36)
    finish = QPoint(int(strip.beat_x(9.3)), 36)
    QTest.mousePress(strip, Qt.LeftButton, pos=start)
    QTest.mouseMove(strip, finish)
    QTest.mouseRelease(strip, Qt.LeftButton, pos=finish)
    moved = marker_items(app.project)[0]
    assert (moved.start_beat, moved.end_beat) == (7, 15)
    edge = QPoint(int(strip.beat_x(15)) - 2, 36)
    target = QPoint(int(strip.beat_x(18.3)), 36)
    QTest.mousePress(strip, Qt.LeftButton, pos=edge)
    QTest.mouseMove(strip, target)
    QTest.mouseRelease(strip, Qt.LeftButton, pos=target)
    assert marker_items(app.project)[0].end_beat == 18
    app.undo()
    markers.refresh()
    assert marker_items(app.project)[0] == moved
    assert region.start_beat == 4


def test_strip_zoom_scroll_alignment_and_long_timeline_extent(window):
    app, _, markers = window
    create_marker(app.project, "region", "Far ending", 20_000, 20_016)
    markers.refresh()
    assert app.playlist.minimumSizeHint().width() >= app.playlist.beat_to_x(20_016)
    app.zoom.setValue(40)
    app.song_scroll.horizontalScrollBar().setValue(100)
    assert (
        markers.strip.beat_x(4)
        == app.playlist.beat_to_x(4) - app.song_scroll.horizontalScrollBar().value()
    )
    assert markers.strip.x_beat(markers.strip.beat_x(100)) == pytest.approx(100)
    # Paint an exposed slice without rasterizing the huge content widget.
    image = markers.strip.grab()
    assert not image.isNull()


def test_edit_dialog_rejects_invalid_region_and_accepts_real_color_picker(window, monkeypatch):
    app, _, _ = window
    dialog = ui.MarkerEditDialog(TimelineMarker("x", "region", "Range", 4, 8), app)
    dialog.end.setValue(3)
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    assert "after" in dialog.error.text()
    dialog.end.setValue(8)
    monkeypatch.setattr(ui.QColorDialog, "getColor", lambda *_: QColor("#123456"))
    QTest.mouseClick(dialog.color_button, Qt.LeftButton)
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.value().color == "#123456"


def test_cancel_or_project_replacement_during_dialog_cannot_add_to_new_session(window, monkeypatch):
    app, _, markers = window
    before = len(app._undo)
    monkeypatch.setattr(ui.MarkerEditDialog, "exec", lambda *_: QDialog.Rejected)
    assert markers.add() is None
    assert len(app._undo) == before

    def replace_session(dialog):
        app.project = Project(name="Other session")
        return QDialog.Accepted

    monkeypatch.setattr(ui.MarkerEditDialog, "exec", replace_session)
    assert markers.add() is None
    assert marker_items(app.project) == []


def test_project_replacement_during_drag_cannot_overwrite_reopened_entry(window):
    app, _, markers = window
    create_marker(app.project, "region", "Saved region", 4, 12)
    markers.refresh()
    strip = markers.strip
    strip.resize(900, 50)
    app.playlist.snap = 1
    start = QPoint(int(strip.beat_x(6)), 36)
    finish = QPoint(int(strip.beat_x(9)), 36)
    QTest.mousePress(strip, Qt.LeftButton, pos=start)
    QTest.mouseMove(strip, finish)
    app.project = Project.from_dict(app.project.to_dict())
    QTest.mouseRelease(strip, Qt.LeftButton, pos=finish)
    assert marker_items(app.project)[0].start_beat == 4


def test_export_extension_must_match_metadata_format(window, monkeypatch, tmp_path):
    app, _, markers = window
    create_marker(app.project, "cue", "Cue", 4)
    csv_path = tmp_path / "markers.csv"
    monkeypatch.setattr(
        ui.QFileDialog, "getSaveFileName", lambda *_: (str(csv_path), "JSON (*.json)")
    )
    assert markers.export() == csv_path
    assert csv_path.read_text().startswith("id,kind,name,")
    invalid = tmp_path / "take.wav"
    monkeypatch.setattr(
        ui.QFileDialog, "getSaveFileName", lambda *_: (str(invalid), "JSON (*.json)")
    )
    with pytest.raises(ValueError, match=".json or .csv"):
        markers.export()
    assert not invalid.exists()


def test_region_paint_uses_its_own_color_not_previous_cue_brush(window):
    app, _, markers = window
    create_marker(app.project, "marker", "Point", 0, color="#ff0000")
    create_marker(app.project, "region", "R", 0, 4, color="#00ff00")
    markers.refresh()
    markers.strip.resize(600, 50)
    image = markers.strip.grab().toImage()
    pixel = image.pixelColor(int(markers.strip.beat_x(3)), 44)
    assert pixel.green() > pixel.red()
    assert pixel.green() > pixel.blue()
