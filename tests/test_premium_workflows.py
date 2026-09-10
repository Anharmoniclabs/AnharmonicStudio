from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpclab.engine import Engine
from mpclab.premium_workflows import attach_premium_workflows, install_premium_runtime
from mpclab.routing_ui import attach_routing_ui
from mpclab.ui.main_window import MainWindow
from mpclab.workflow_compat import restore_unmanaged_legacy_shortcuts


def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Engine, "start", lambda _engine: None)
    install_premium_runtime()
    window = MainWindow(tmp_path, restore_session=False)
    controller = attach_premium_workflows(window)
    attach_routing_ui(window, controller)
    restore_unmanaged_legacy_shortcuts(controller)
    window.show()
    app.processEvents()
    return app, window, controller


def test_controller_adds_workflow_and_routing_menus_and_command_catalog(tmp_path, monkeypatch):
    _app, window, controller = make_window(tmp_path, monkeypatch)
    try:
        assert controller.registry.get("clip.stretch").title == "Time-stretch selected clip"
        assert controller.registry.get("notes.scale_lock").title == "Lock selected notes to scale"
        assert controller.registry.get("mixer.bus_create").title == "Create routing bus"
        menus = {action.text() for action in window.menuBar().actions()}
        assert {"Workflow", "Routing"} <= menus
        assert window.routing_controller is not None
        assert hasattr(window.engine, "prepare_routing")
        assert hasattr(window.engine, "plugin_pdc")
    finally:
        window.close()


def test_custom_key_binding_owns_gesture_before_main_window(tmp_path, monkeypatch):
    app, window, controller = make_window(tmp_path, monkeypatch)
    positions = []
    window.engine.set_position = positions.append
    try:
        controller.registry.bind("transport.rewind", "Ctrl+Alt+H")
        controller._reindex_bindings()
        QTest.keyClick(window, Qt.Key_H, Qt.ControlModifier | Qt.AltModifier)
        app.processEvents()
        assert positions == [0.0]
    finally:
        window.close()


def test_unmanaged_ctrl_number_workspace_keys_remain_enabled(tmp_path, monkeypatch):
    _app, window, controller = make_window(tmp_path, monkeypatch)
    try:
        ctrl_one = [
            shortcut for shortcut in window._shortcuts if shortcut.key().toString() == "Ctrl+1"
        ]
        assert ctrl_one and ctrl_one[0].isEnabled()
        managed_five = [
            shortcut for shortcut in window._shortcuts if shortcut.key().toString() == "F5"
        ]
        assert managed_five and not managed_five[0].isEnabled()
    finally:
        window.close()
