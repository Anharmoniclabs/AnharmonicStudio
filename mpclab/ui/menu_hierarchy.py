"""Arrange feature-owned actions without replacing their signals or shortcuts."""

from PySide6.QtCore import QEvent, QObject


class _PanelButtonState(QObject):
    """Keep header toggles truthful when shortcuts or focus mode change panels."""

    def __init__(self, panel, button):
        super().__init__(panel)
        self.button = button
        self.panel = panel
        panel.installEventFilter(self)
        self.sync()

    def sync(self):
        self.button.setChecked(not self.panel.isHidden())

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Show, QEvent.Hide):
            self.sync()
        return False


def organize_menus(window, controller):
    """Run after features attach; retain the original QMenus for lazy refreshes."""
    if getattr(window, "_menus_organized", False):
        return
    bar = window.menuBar()
    # Retain QAction wrappers: releasing them while moving their menus can
    # invalidate the associated QMenu in PySide. Keep both for window lifetime.
    window._header_menu_actions = list(bar.actions())
    menus = {a.text().replace("&", ""): a.menu() for a in window._header_menu_actions if a.menu()}
    window._header_menus = menus
    sound = bar.addMenu("Sound")
    menus["Sound"] = sound
    for source, destination, title in (
        ("MIDI files", "File", "MIDI files"),
        ("Loudness delivery", "File", "Mastering & delivery"),
        ("Recording", "Transport", "Recording & takes"),
        ("Markers", "Transport", "Markers"),
        ("Instruments", "Sound", "Instruments"),
        ("Plugins", "Sound", "Plugins"),
        ("Routing", "Sound", "Routing"),
        ("AUDIO", "Sound", "Devices & connections"),
        ("Audio engine", "Sound", "Audio engine"),
        ("Workflow", "Tools", "Workflow"),
    ):
        menu = menus.get(source)
        if menu is not None:
            bar.removeAction(menu.menuAction())
            menu.setTitle(title)
            menus[destination].addMenu(menu)

    # Keep all extension-provided actions, including actions added after the
    # original menus were constructed. Only move the known base View commands.
    view = menus["View"]
    original = list(view.actions())
    workspaces = view.addMenu("Workspaces")
    panels = view.addMenu("Panels & keyboard")
    appearance = view.addMenu("Appearance")
    for index, action in enumerate(original):
        target = workspaces if index < window.tabs.count() else panels
        if action.text().startswith(("Light / dark", "Color wheel")):
            target = appearance
        view.removeAction(action)
        target.addAction(action)
    appearance.addAction("Reset dark blue", window.reset_appearance)

    # Device setup belongs beside routing, not among editing commands.
    for action in list(menus["Tools"].actions()):
        if action.text() in ("Audio setup…", "Devices & Plugins…"):
            menus["Tools"].removeAction(action)
            sound.addAction(action)
    for name in ("File", "Edit", "View", "Transport", "Sound", "Tools", "Help"):
        action = menus[name].menuAction()
        bar.removeAction(action)
        bar.addAction(action)

    # Replace the second File-like dropdown with the existing command search.
    button = window.project_menu_button
    button.setMenu(None)
    button.setText("Commands…")
    button.setToolTip("Find an action by name: quantize, route, record, export…")
    button.setAccessibleName("Search Studio commands")
    button.clicked.connect(controller.show_command_palette)
    window._menus_organized = True
