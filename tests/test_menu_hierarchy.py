"""Menu regrouping preserves live actions and never touches transport."""

from types import SimpleNamespace

from PySide6.QtWidgets import QMainWindow, QPushButton, QTabWidget, QWidget

from mpclab.ui.menu_hierarchy import organize_menus


def test_menu_tree_preserves_callbacks_lazy_refresh_and_is_idempotent():
    window = QMainWindow()
    window.tabs = QTabWidget()
    window.tabs.addTab(QWidget(), "Song")
    window.project_menu_button = QPushButton("Project", window)
    window.reset_appearance = lambda: None
    calls = []
    controller = SimpleNamespace(show_command_palette=lambda: calls.append("search"))
    titles = (
        "File",
        "Edit",
        "View",
        "Transport",
        "Tools",
        "Help",
        "AUDIO",
        "Workflow",
        "Routing",
        "Plugins",
        "Recording",
        "Markers",
        "MIDI files",
        "Instruments",
        "Loudness delivery",
        "Audio engine",
    )
    menus = {title: window.menuBar().addMenu(title) for title in titles}
    originals = []
    for title, menu in menus.items():
        action = menu.addAction(title + " action")
        action.triggered.connect(lambda _=False, name=title: calls.append(name))
        originals.append(action)
    menus["AUDIO"].aboutToShow.connect(lambda: calls.append("scan"))
    organize_menus(window, controller)
    organize_menus(window, controller)
    assert [a.text() for a in window.menuBar().actions()] == [
        "File",
        "Edit",
        "View",
        "Transport",
        "Sound",
        "Tools",
        "Help",
    ]
    assert calls == []

    def leaves(menu):
        return [
            leaf
            for action in menu.actions()
            for leaf in (leaves(action.menu()) if action.menu() else [action])
        ]

    reachable = [leaf for root in window.menuBar().actions() for leaf in leaves(root.menu())]
    for action in originals:
        assert reachable.count(action) == 1
        action.trigger()
    assert calls == list(titles)
    menus["AUDIO"].aboutToShow.emit()
    window.project_menu_button.click()
    assert calls[-2:] == ["scan", "search"]
    window.deleteLater()
