"""Opt-in access to the current session; never launches or moves a browser."""

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt
from .server import Companion


def start_companion(window, port=0):
    QApplication.setAttribute(Qt.AA_DontUseNativeDialogs)
    companion = getattr(window, "_browser_companion", None)
    if companion is None:
        companion = window._browser_companion = Companion(window, port)
    return companion


def install_companion_action(window):
    action = QAction("Connect local browser…", window)

    def connect():
        companion = start_companion(window)
        dialog = QDialog(window)
        dialog.setWindowTitle("Local browser companion")
        layout = QVBoxLayout(dialog)
        label = QLabel(
            "Open this private link in a browser on this computer.\nAudio and files use the local workstation."
        )
        layout.addWidget(label)
        link = QLineEdit(companion.url)
        link.setReadOnly(True)
        link.setMinimumWidth(620)
        link.setAccessibleName("Private browser connection link")
        link.selectAll()
        layout.addWidget(link)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        copy = buttons.addButton("Copy link", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(companion.url))
        stop = buttons.addButton("Disconnect browsers", QDialogButtonBox.ActionRole)

        def disconnect():
            companion.close()
            window._browser_companion = None
            dialog.accept()

        stop.clicked.connect(disconnect)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    action.triggered.connect(connect)
    menus = window.menuBar().actions()
    if menus and menus[0].menu():
        menus[0].menu().addAction(action)
    else:
        window.menuBar().addAction(action)
