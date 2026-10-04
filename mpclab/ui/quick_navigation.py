"""Qt Quick workspace navigation over the existing workstation commands.

QML owns presentation only. It never owns projects, playback or audio objects.
The widget navigation remains available if the scene cannot be loaded/rendered.
"""

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import QSizePolicy

from . import theme


class QuickNavigation(QQuickWidget):
    activated = Signal(int)
    unavailable = Signal(str)

    def __init__(self, entries, parent=None, source=None):
        super().__init__(parent)
        self.setObjectName("quickWorkspaceNavigation")
        self.setAccessibleName("Studio workspaces")
        self.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.setFixedHeight(46)
        self.sceneGraphError.connect(lambda _error, message: self.unavailable.emit(message))
        self.setSource(
            QUrl.fromLocalFile(
                str(source or Path(__file__).with_name("qml") / "WorkspaceNavigation.qml")
            )
        )
        root = self.rootObject()
        if root is not None:
            root.setProperty("workspaces", entries)
            root.activated.connect(self.activated.emit)
        self.refresh_theme()

    @property
    def ready(self):
        return self.status() == QQuickWidget.Ready

    def select(self, page):
        if self.rootObject() is not None:
            self.rootObject().setProperty("selectedPage", page)

    def refresh_theme(self):
        self.setClearColor(QColor(theme.C["bg2"]))
        root = self.rootObject()
        if root is not None:
            for name, token in (
                ("surface", "bg2"),
                ("foreground", "fg"),
                ("muted", "dim"),
                ("accent", "accent"),
                ("selection", "item_sel"),
                ("hoverSurface", "hover"),
                ("divider", "line"),
            ):
                root.setProperty(name, QColor(theme.C[token]))
            root.setProperty("uiFont", theme.UI_FAMILIES[0])
