"""Inspect the local display before building UI; keep every editor reachable.

Qt geometry is already in logical pixels: never apply devicePixelRatio twice.
No network, privileged probes, hardware identifiers or audio streams are used.
"""

from dataclasses import asdict, dataclass
import json
import os
import platform

from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class HardwareProfile:
    system: str
    architecture: str
    cpu_threads: int
    memory_bytes: int | None
    width: int
    height: int
    scale: float

    @property
    def compact(self):
        return self.width < 1440 or self.height < 850

    @property
    def initial_size(self):
        return min(1680, max(320, self.width - 32)), min(950, max(240, self.height - 64))


def inspect_hardware(screen=None):
    app = QApplication.instance()
    screen = screen or (app.primaryScreen() if app else None)
    rect = screen.availableGeometry() if screen else None
    try:
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        memory = None
    return HardwareProfile(
        platform.system(),
        platform.machine(),
        os.cpu_count() or 1,
        memory,
        rect.width() if rect else 1280,
        rect.height() if rect else 720,
        screen.devicePixelRatio() if screen else 1.0,
    )


class EditorViewport(QScrollArea):
    """One stable owner per editor, with overflow reachable at any window size."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QScrollArea.NoFrame)
        self.setWidgetResizable(True)
        self.setMinimumSize(0, 0)

    def mount(self, page):
        if self.widget() is not page:
            self.setWidget(page)
        page.show()


class WorkspaceFit(QObject):
    """Follow monitor/work-area changes and fit application-owned dialogs."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.screen = None
        QApplication.instance().installEventFilter(self)

    def attach(self):
        handle = self.window.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(self.screen_changed)
            self.screen_changed(handle.screen())

    def screen_changed(self, screen):
        if self.screen is not None:
            self.screen.availableGeometryChanged.disconnect(self.refit)
        self.screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self.refit)
        self.refit()

    def refit(self, *_args):
        from shiboken6 import isValid

        if not isValid(self.window):
            return
        self.window.hardware_profile = inspect_hardware(self.screen)
        fit_to_screen(self.window, self.screen)
        self.window._sync_responsive_panels()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QDialog):
            # Native file pickers and message boxes manage their own content.
            if obj.window() is obj and not isinstance(obj, (QFileDialog, QMessageBox)):
                if obj.parentWidget() is not None and obj.parentWidget().window() is self.window:
                    self.fit_dialog(obj)
        return False

    def fit_dialog(self, dialog):
        if not dialog.property("workspaceScrollable") and dialog.layout() is not None:
            content = QWidget()
            content.setLayout(dialog.layout())
            viewport = EditorViewport()
            viewport.mount(content)
            outer = QVBoxLayout(dialog)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(viewport)
            dialog.setProperty("workspaceScrollable", True)
            dialog.setMinimumSize(0, 0)
        fit_to_screen(dialog, self.screen)


def fit_to_screen(widget, screen=None):
    screen = screen or widget.screen()
    if screen is None:
        return
    rect = screen.availableGeometry()
    width, height = max(1, rect.width() - 24), max(1, rect.height() - 56)
    widget.setMinimumSize(min(widget.minimumWidth(), width), min(widget.minimumHeight(), height))
    if not widget.isMaximized() and not widget.isFullScreen():
        widget.resize(min(widget.width(), width), min(widget.height(), height))
        # Wayland positions top-level windows itself. Other backends need this
        # for saved/off-screen positions after a monitor is disconnected.
        if not QApplication.platformName().startswith("wayland"):
            widget.move(
                max(rect.left(), min(widget.x(), rect.right() - widget.width())),
                max(rect.top(), min(widget.y(), rect.bottom() - widget.height() - 32)),
            )


def write_profile(profile, root):
    try:
        (root / "hardware-profile.json").write_text(json.dumps(asdict(profile), indent=2) + "\n")
    except OSError:
        pass  # Diagnostics must never prevent opening a session.
