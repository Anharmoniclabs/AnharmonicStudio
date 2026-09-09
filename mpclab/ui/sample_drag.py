"""Sample-range/file drags and navigation between musical destinations."""

from .window_client import WindowClient

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication, QPushButton

from .arrangement_tools import enter_song_mode
from .sample_file_drop import local_audio_paths, send_local_files
from .sample_workflow import valid_sample_range

RANGE_MIME = "application/x-mpclab-range"
CLIP_MIME = "application/x-mpclab-clip"


def sample_range(mime, library):
    """Decode a known source and reject malformed or inaudible trims."""
    try:
        if mime.hasFormat(RANGE_MIME):
            ref, a, b = bytes(mime.data(RANGE_MIME)).decode().split("|")
            start, end = float(a), float(b)
        elif mime.hasFormat(CLIP_MIME):
            ref = bytes(mime.data(CLIP_MIME)).decode()
            start, end = 0.0, library.clips[ref].duration
        else:
            return None
        clip = library.clips[ref]
        bounds = valid_sample_range(clip, start, end, clip.sample_rate)
        return (ref, *bounds) if bounds else None
    except (ValueError, KeyError, UnicodeError):
        return None


def send_local_to_arrange(app, paths):
    """Import one or more local songs/samples and append them to the song timeline."""
    if not paths:
        return False
    imported = app.browser.import_paths([str(path) for path in paths], select_imported=False)
    placed = []
    for clip in imported:
        block = app.append_sample_to_arrangement(clip.id, 0.0, clip.duration)
        if block is not None:
            placed.append(block)
    if not placed:
        return False
    enter_song_mode(app)
    app.status.showMessage(
        f"{len(placed)} audio file{'s' if len(placed) != 1 else ''} mapped into Arrange · "
        "SONG mode plays the full timeline",
        6500,
    )
    return True


class SampleDragButton(QPushButton):
    """Retain click-to-audition while allowing a slice to be picked up."""

    def __init__(self, text, start_drag, parent=None):
        super().__init__(text, parent)
        self.start_drag = start_drag
        self._press = None

    def mousePressEvent(self, event):
        self._press = event.position().toPoint() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            distance = (event.position().toPoint() - self._press).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._press = None
                self.setDown(False)
                self.start_drag()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)


class ArrangeDropFilter(WindowClient, QObject):
    """Reveal musical destinations during a drag without modifying the project."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.pending = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.reveal)
        self.tab_bar = app.tabs.tabBar()
        self.targets = {app.studio.buttons[i]: i for i in (1, 2, 6)}
        for widget in (self.tab_bar, *self.targets):
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)

    def reveal(self):
        if self.pending is None:
            return
        target, studio = self.pending
        if studio:
            self.app.studio.select(target)
        else:
            self.app.tabs.setCurrentIndex(target)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.DragLeave:
            self.timer.stop()
            self.pending = None
            event.accept()
            return True
        if kind not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        target = (
            self.tab_bar.tabAt(event.position().toPoint())
            if watched is self.tab_bar
            else self.targets.get(watched)
        )
        payload = sample_range(event.mimeData(), self.app.library)
        paths = () if payload is not None else local_audio_paths(event.mimeData())
        valid = payload is not None or bool(paths)
        if not valid or target not in (1, 2, 6):
            self.timer.stop()
            self.pending = None
            if kind == QEvent.DragEnter and valid:
                event.acceptProposedAction()
            else:
                event.ignore()
            return True
        if kind == QEvent.Drop:
            self.timer.stop()
            self.pending = None
            if paths:
                if target == 2:
                    placed = send_local_to_arrange(self.app, paths)
                else:
                    placed = send_local_files(
                        self.app, paths, destination="notes" if target == 6 else "beats"
                    )
                if not placed:
                    event.ignore()
                    return True
            elif target == 2:
                if self.app.append_sample_to_arrangement(*payload) is None:
                    event.ignore()
                    return True
                enter_song_mode(self.app)
            else:
                index = self.app.sample_workflow.send(
                    *payload, destination="notes" if target == 6 else "beats"
                )
                if index is None:
                    event.ignore()
                    return True
        else:
            # Route hover navigation by the widget receiving the drag, not by
            # global Studio visibility. Programmatic tests and transient UI
            # state can legitimately address a Studio destination before the
            # shell's enabled flag catches up; the button itself is definitive.
            pending = (target, watched in self.targets)
            if pending != self.pending:
                self.timer.stop()
                self.pending = pending
                self.timer.start()
            self.app.status.showMessage(
                {
                    1: "Beats · drop to add sounds to the current bank",
                    2: "Arrange · drop to map audio onto the full-song timeline",
                    6: "Notes · drop to create playable sample instruments",
                }[target]
            )
        if paths:
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.acceptProposedAction()
        return True


class SoundDropFilter(WindowClient, QObject):
    """A shared validator for Beats headers and the explicit Notes sound target."""

    def __init__(self, app, widget, destination, target):
        super().__init__(widget)
        self.app, self.widget = app, widget
        self.destination, self.target = destination, target
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.DragLeave:
            self.widget.setProperty("sampleDropActive", False)
            self.widget.update()
            return False
        if kind not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        payload = sample_range(event.mimeData(), self.app.library)
        paths = () if payload is not None else local_audio_paths(event.mimeData())
        index = self.target(event.position())
        if (payload is None and not paths) or index is False:
            if event.mimeData().hasUrls():
                self.app.status.showMessage(
                    "Drop audio files on a lane header or Add sound. "
                    "Extract ZIPs first; add whole folders with + PACK.",
                    6000,
                )
            self.widget.setProperty("sampleDropActive", False)
            event.ignore()
            return True
        if paths and index is not None and len(paths) != 1:
            self.widget.setProperty("sampleDropActive", False)
            self.app.status.showMessage(
                "Drop multiple files on the Add sound area, not an existing lane.", 6000
            )
            event.ignore()
            return True
        message = (
            "Add a new sound"
            if index is None
            else f"Replace {self.app.project.pads[index].name or 'sound'} — keep rhythm and routing"
        )
        self.app.status.showMessage(message)
        if kind == QEvent.Drop:
            self.widget.setProperty("sampleDropActive", False)
            if paths:
                placed = send_local_files(
                    self.app, paths, destination=self.destination, index=index
                )
            else:
                placed = (
                    self.app.sample_workflow.send(
                        *payload, destination=self.destination, index=index
                    )
                    is not None
                )
            if not placed:
                event.ignore()
                return True
        else:
            self.widget.setProperty("sampleDropActive", True)
        self.widget.update()
        if paths:
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.acceptProposedAction()
        return True
