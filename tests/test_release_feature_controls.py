"""Packaged startup must retain native feature wiring, without triggering actions."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QComboBox, QMainWindow, QPushButton, QWidget

from mpclab.release_check import PRODUCTION_COMMANDS, validate_production_features
from mpclab.workflow_commands import CommandRegistry, CommandSpec


@pytest.fixture
def startup_window():
    window = QMainWindow()
    registry = CommandRegistry()

    def forbidden_callback():
        raise AssertionError("Startup validation must not execute a UI command.")

    for command in PRODUCTION_COMMANDS:
        registry.register(CommandSpec(command, command, forbidden_callback))
    window.automation_mode_controller = SimpleNamespace(mode_combo=QComboBox(window))
    window._track_management_attached = True
    window.mixer = SimpleNamespace(add_track_button=QPushButton("+ TRACK", window))
    panel = QWidget(window)
    panel.setObjectName("timelineMarkerTracks")
    strip = QWidget(panel)
    strip.setObjectName("timelineMarkerStrip")
    window.timeline_marker_controller = SimpleNamespace(
        panel=panel,
        strip=strip,
        buttons={
            command: QPushButton(command, panel)
            for command in PRODUCTION_COMMANDS
            if command.startswith("markers.")
        },
    )
    menu = window.menuBar().addMenu("&File")
    menu_action = menu.menuAction()
    window.audio_analysis_controller = SimpleNamespace(
        file_menu=menu,
        file_menu_action=menu_action,
        action=menu.addAction("Analyze rendered audio…"),
    )
    return window, SimpleNamespace(registry=registry)


def test_packaged_control_validation_does_not_execute_commands(startup_window):
    window, controller = startup_window
    assert validate_production_features(window, controller) == list(PRODUCTION_COMMANDS)
    assert "mixer.track_add" in PRODUCTION_COMMANDS
    assert "markers.export" in PRODUCTION_COMMANDS
    assert "audio.analyze_file" in PRODUCTION_COMMANDS


@pytest.mark.parametrize("command", ["mixer.track_add", "markers.add", "audio.analyze_file"])
def test_packaged_validation_rejects_missing_commands(startup_window, command):
    window, controller = startup_window
    del controller.registry._commands[command]
    with pytest.raises(RuntimeError, match="Production command is not attached"):
        validate_production_features(window, controller)


@pytest.mark.parametrize("feature", ["automation", "mixer", "strip", "marker_button", "analyzer"])
def test_packaged_validation_rejects_missing_or_disabled_controls(startup_window, feature):
    window, controller = startup_window
    if feature == "automation":
        window.automation_mode_controller = None
    elif feature == "mixer":
        window.mixer.add_track_button.setEnabled(False)
    elif feature == "strip":
        window.timeline_marker_controller.strip.setObjectName("missing")
    elif feature == "marker_button":
        window.timeline_marker_controller.buttons["markers.cue"].setEnabled(False)
    else:
        window.audio_analysis_controller.action.setEnabled(False)
    with pytest.raises(RuntimeError, match="Production"):
        validate_production_features(window, controller)
