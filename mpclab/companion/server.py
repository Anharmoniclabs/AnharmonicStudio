"""Local companion. Routes input to application-owned Qt widgets only.

No OS keyboard injection, screen capture, shell execution, or window-manager
control. Audio and files belong to the local workstation engine.
"""

from __future__ import annotations

import json
import queue
import time
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent, QInputMethodEvent, QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication


class LimitedHTTPServer(ThreadingHTTPServer):
    """Bound worker count even for clients that never finish their headers."""

    daemon_threads = True
    max_workers = 8

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(self.max_workers)
        super().__init__(*args)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(2.0)
        return request, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Companion(QObject):
    def __init__(self, window, port=0):
        super().__init__(window)
        self.window = window
        self.token = secrets.token_urlsafe(32)
        self.frames = {}
        self.widgets = {}
        self.lock = threading.Lock()
        self._closed = False
        self.pending = queue.Queue(maxsize=128)
        self._held = {}
        self._overflow = False
        self.last_seen = time.monotonic()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Tokens and user input must not appear in access logs.

            def reply(self, status, data=b"", content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
                )
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def allowed(self):
                return (
                    self.headers.get("Host") == owner.address
                    and self.headers.get("Origin", owner.origin) == owner.origin
                )

            def authenticated(self):
                provided = self.headers.get("Authorization", "").encode("utf-8")
                with owner.lock:
                    valid = (
                        not owner._closed
                        and self.allowed()
                        and secrets.compare_digest(
                            provided, ("Bearer " + owner.token).encode("ascii")
                        )
                    )
                    if valid:
                        owner.last_seen = time.monotonic()
                    return valid

            def do_GET(self):
                if not self.allowed():
                    self.reply(403)
                    return
                path = urlparse(self.path).path
                static = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/client.js": ("client.js", "text/javascript"),
                    "/style.css": ("style.css", "text/css"),
                }
                if path in static:
                    name, mime = static[path]
                    self.reply(200, (Path(__file__).parent / name).read_bytes(), mime)
                elif not self.authenticated():
                    self.reply(401)
                elif path == "/surfaces":
                    with owner.lock:
                        surfaces = [
                            {k: v for k, v in f.items() if k != "jpeg"}
                            for f in owner.frames.values()
                        ]
                    self.reply(200, json.dumps(surfaces).encode())
                elif path.startswith("/frame/"):
                    with owner.lock:
                        frame = owner.frames.get(path.removeprefix("/frame/"), {}).get("jpeg")
                    self.reply(200 if frame else 404, frame or b"", "image/jpeg")
                else:
                    self.reply(404)

            def do_POST(self):
                if not self.authenticated():
                    self.reply(401)
                    return
                try:
                    if (
                        self.headers.get("Transfer-Encoding")
                        or len(self.headers.get_all("Content-Length", [])) != 1
                    ):
                        raise ValueError("ambiguous request framing")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16384 or self.path != "/input":
                        raise ValueError("invalid input request")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError("truncated body")
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("invalid input object")
                    self.reply(202 if owner.enqueue(payload) else 429, b"{}")
                except (ValueError, UnicodeError, RecursionError, TimeoutError):
                    self.reply(400)

        self.server = LimitedHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.address = f"127.0.0.1:{self.server.server_port}"
        self.origin = "http://" + self.address
        self.url = self.origin + "/#" + self.token
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True, name="studio-browser-companion"
        )
        self.thread.start()
        self.input_timer = QTimer(self)
        self.input_timer.timeout.connect(self.drain)
        self.input_timer.start(8)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.capture)
        self.timer.start(100)
        QApplication.instance().aboutToQuit.connect(self.close)

    def close(self):
        if getattr(self, "_closed", False):
            return
        with self.lock:
            self._closed = True
            self.token = secrets.token_urlsafe(32)
            self.frames = {}
            self.widgets = {}
            while not self.pending.empty():
                self.pending.get_nowait()
        self.release_inputs()
        self.input_timer.stop()
        self.timer.stop()
        self.server.shutdown()
        self.server.server_close()

    def enqueue(self, event):
        with self.lock:
            if self._closed:
                return False
            try:
                self.pending.put_nowait(event)
                return True
            except queue.Full:
                self._overflow = True
                return False

    def drain(self):
        if self._closed:
            return
        with self.lock:
            reset = self._overflow
            self._overflow = False
            if reset:
                while not self.pending.empty():
                    self.pending.get_nowait()
        if reset or time.monotonic() - self.last_seen > 3:
            self.release_inputs()
        for _ in range(32):
            try:
                event = self.pending.get_nowait()
            except queue.Empty:
                break
            self.dispatch(event)

    def release_inputs(self):
        for target, code, mods, text in list(self._held.values()):
            try:
                QApplication.sendEvent(target, QKeyEvent(QKeyEvent.KeyRelease, code, mods, text))
            except RuntimeError:
                pass
        self._held.clear()
        target = getattr(self, "_drag_target", None)
        self._drag_target = None
        if target is not None:
            try:
                point = QPoint(-1, -1)
                QApplication.sendEvent(
                    target,
                    QMouseEvent(
                        QMouseEvent.MouseButtonRelease,
                        point,
                        target.mapToGlobal(point),
                        Qt.LeftButton,
                        Qt.NoButton,
                        Qt.NoModifier,
                    ),
                )
            except RuntimeError:
                pass

    def owned(self, widget):
        return self.belongs(widget, self.window)

    @staticmethod
    def belongs(widget, root):
        while widget is not None:
            if widget is root:
                return True
            widget = widget.parentWidget()
        return False

    def capture(self):
        if self._closed:
            return
        surfaces, widgets = {}, {}
        for widget in QApplication.topLevelWidgets():
            if not widget.isVisible() or not self.owned(widget):
                continue
            key = str(id(widget))
            image = QByteArray()
            buffer = QBuffer(image)
            buffer.open(QIODevice.WriteOnly)
            widget.grab().save(buffer, "JPEG", 85)
            buffer.close()
            surfaces[key] = dict(
                id=key,
                title=widget.windowTitle() or type(widget).__name__,
                width=widget.width(),
                height=widget.height(),
                jpeg=bytes(image),
                modal=widget.isModal(),
                main=widget is self.window,
            )
            widgets[key] = widget
        self.widgets = widgets
        with self.lock:
            self.frames = surfaces

    def dispatch(self, event):
        """Validate untrusted browser messages on the owning GUI thread."""
        if self._closed or not isinstance(event, dict):
            return
        try:
            # Release goes to the widget that received the press, even if a
            # modal opened or focus moved while the key was held.
            held_key = (str(event.get("surface")), str(event.get("key", "")))
            if event.get("type") == "keyup" and held_key in self._held:
                target, code, mods, text = self._held.pop(held_key)
                QApplication.sendEvent(target, QKeyEvent(QKeyEvent.KeyRelease, code, mods, text))
                return
            if event.get("type") == "release":
                self.release_inputs()
                return
            surface = self.widgets.get(str(event.get("surface")))
            if surface is None or not self.owned(surface) or not surface.isVisible():
                return
            modal = QApplication.activeModalWidget()
            if modal and self.owned(modal) and not self.belongs(surface, modal):
                return
            kind = event.get("type")
            mods = Qt.NoModifier
            for flag, qt in (
                ("ctrl", Qt.ControlModifier),
                ("shift", Qt.ShiftModifier),
                ("alt", Qt.AltModifier),
                ("meta", Qt.MetaModifier),
            ):
                if event.get(flag) is True:
                    mods |= qt
            if kind in ("down", "up", "move", "wheel", "double", "context"):
                x, y = int(event["x"]), int(event["y"])
                if not (0 <= x < surface.width() and 0 <= y < surface.height()):
                    return
                point = QPoint(x, y)
                target = surface.childAt(point) or surface
                if kind == "down":
                    self._drag_target = target
                    target.setFocus(Qt.MouseFocusReason)
                elif kind in ("move", "up"):
                    target = getattr(self, "_drag_target", None) or target
                if not self.owned(target) or (
                    modal and self.owned(modal) and not self.belongs(target, modal)
                ):
                    self._drag_target = None
                    return
                global_pos = surface.mapToGlobal(point)
                local = target.mapFromGlobal(global_pos)
                button = Qt.RightButton if event.get("button") == 2 else Qt.LeftButton
                if kind == "context":
                    qt_event = QContextMenuEvent(QContextMenuEvent.Mouse, local, global_pos, mods)
                elif kind == "wheel":
                    delta = max(-1200, min(1200, int(event.get("delta", 0))))
                    qt_event = QWheelEvent(
                        local,
                        global_pos,
                        QPoint(),
                        QPoint(0, delta),
                        Qt.NoButton,
                        mods,
                        Qt.NoScrollPhase,
                        False,
                    )
                else:
                    types = {
                        "down": QMouseEvent.MouseButtonPress,
                        "double": QMouseEvent.MouseButtonDblClick,
                        "up": QMouseEvent.MouseButtonRelease,
                        "move": QMouseEvent.MouseMove,
                    }
                    qt_event = QMouseEvent(
                        types[kind],
                        local,
                        global_pos,
                        button if kind != "move" else Qt.NoButton,
                        button
                        if kind == "down" or (kind == "move" and event.get("buttons"))
                        else Qt.NoButton,
                        mods,
                    )
                QApplication.sendEvent(target, qt_event)
                if kind == "up":
                    self._drag_target = None
            elif kind == "text":
                target = surface.focusWidget()
                text = event.get("text", "")
                if target is not None and isinstance(text, str) and len(text) <= 4096:
                    commit = QInputMethodEvent()
                    commit.setCommitString(text)
                    QApplication.sendEvent(target, commit)
            elif kind in ("keydown", "keyup"):
                target = surface.focusWidget() or surface
                key = str(event.get("key", ""))[:32]
                keys = {
                    "Enter": Qt.Key_Return,
                    "Escape": Qt.Key_Escape,
                    "Backspace": Qt.Key_Backspace,
                    "Tab": Qt.Key_Tab,
                    "Delete": Qt.Key_Delete,
                    "ArrowLeft": Qt.Key_Left,
                    "ArrowRight": Qt.Key_Right,
                    "ArrowUp": Qt.Key_Up,
                    "ArrowDown": Qt.Key_Down,
                    "Home": Qt.Key_Home,
                    "End": Qt.Key_End,
                    "PageUp": Qt.Key_PageUp,
                    "PageDown": Qt.Key_PageDown,
                }
                code = keys.get(
                    key, ord(key.upper()) if len(key) == 1 and len(key.upper()) == 1 else 0
                )
                if code:
                    if kind == "keydown":
                        if len(self._held) >= 128 and held_key not in self._held:
                            self.release_inputs()
                        self._held.setdefault(
                            held_key, (target, code, mods, key if len(key) == 1 else "")
                        )
                    QApplication.sendEvent(
                        target,
                        QKeyEvent(
                            QKeyEvent.KeyPress if kind == "keydown" else QKeyEvent.KeyRelease,
                            code,
                            mods,
                            key if len(key) == 1 else "",
                            bool(event.get("repeat")),
                        ),
                    )
        except (KeyError, ValueError, TypeError, OverflowError, RuntimeError):
            # Malformed input or a dialog deleted while an input was queued.
            return
