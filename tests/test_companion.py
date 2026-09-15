"""Companion security boundaries and application-only input routing."""

import json
import urllib.request
import urllib.error
import pytest
from PySide6.QtWidgets import QWidget, QLineEdit, QVBoxLayout, QApplication


@pytest.fixture
def qapp():
    return QApplication.instance()


from mpclab.companion.server import Companion


@pytest.fixture
def companion(qapp):
    window = QWidget()
    window.resize(400, 180)
    layout = QVBoxLayout(window)
    edit = QLineEdit()
    layout.addWidget(edit)
    window.show()
    qapp.processEvents()
    server = Companion(window)
    server.capture()
    yield server, edit
    server.close()
    window.close()


def test_companion_auth_and_origin(companion):
    server, _ = companion
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(server.origin + "/surfaces")
    assert error.value.code == 401
    request = urllib.request.Request(
        server.origin + "/surfaces", headers={"Authorization": "Bearer " + server.token}
    )
    with urllib.request.urlopen(request) as response:
        assert json.load(response)[0]["width"] == 400
    request.add_header("Origin", "https://untrusted.example")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403


def test_companion_inputs_only_owned_widgets(companion, qapp):
    server, edit = companion
    edit.setFocus()
    qapp.processEvents()
    surface = str(id(server.window))
    server.dispatch(dict(surface=surface, type="keydown", key="x"))
    assert edit.text() == "x"
    server.dispatch(dict(surface="not-owned", type="keydown", key="y"))
    assert edit.text() == "x"
    server.dispatch(dict(surface=surface, type="down", x="NaN", y=0))
    server.dispatch(dict(surface=surface, type="keydown", key="ß"))


def test_revocation_rejects_queued_and_direct_input(companion):
    server, edit = companion
    edit.setFocus()
    event = dict(surface=str(id(server.window)), type="keydown", key="x")
    assert server.enqueue(event)
    server.close()
    assert not server.enqueue(event)
    server.dispatch(event)
    server.drain()
    assert edit.text() == ""
    assert not server.frames and server.pending.empty()


def test_bounded_input_admission_and_overload_release(companion):
    server, edit = companion
    event = dict(surface=str(id(server.window)), type="keydown", key="x")
    for _ in range(server.pending.maxsize):
        assert server.enqueue(event)
    assert not server.enqueue(event)
    server.drain()
    assert server.pending.empty()
    assert edit.text() == ""


def test_malformed_events_do_not_escape_qt_dispatch(companion):
    server, _ = companion
    for event in (None, {}, [], dict(surface=str(id(server.window)), type="down")):
        server.dispatch(event)


def test_key_release_reaches_original_widget_after_focus_changes(companion, qapp):
    from PySide6.QtCore import QObject, QEvent

    class Watch(QObject):
        def __init__(self):
            super().__init__()
            self.releases = 0

        def eventFilter(self, obj, event):
            if event.type() == QEvent.KeyRelease:
                self.releases += 1
            return False

    server, edit = companion
    watcher = Watch()
    edit.installEventFilter(watcher)
    edit.setFocus()
    qapp.processEvents()
    base = dict(surface=str(id(server.window)), key="x")
    server.dispatch(dict(base, type="keydown"))
    other = QLineEdit(server.window)
    other.show()
    other.setFocus()
    qapp.processEvents()
    server.dispatch(dict(base, type="keyup"))
    assert watcher.releases == 1
    assert not server._held


def test_non_ascii_authorization_is_rejected_cleanly(companion):
    server, _ = companion
    request = urllib.request.Request(
        server.origin + "/surfaces", headers={"Authorization": "Bearer \u00e9"}
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == 401


def test_partial_requests_cannot_create_unbounded_workers(companion):
    import socket
    import time

    server, _ = companion
    connections = []
    try:
        for _ in range(server.server.max_workers + 4):
            connection = socket.create_connection(
                ("127.0.0.1", server.server.server_port), timeout=1
            )
            connections.append(connection)
            connection.sendall(b"GET / HTTP/1.1\r\n")
        time.sleep(0.1)
        assert server.server.slots._value == 0
        # Threads have a two-second read deadline; the bound recovers without
        # requiring the user to restart Studio.
        time.sleep(2.2)
        assert server.server.slots._value == server.server.max_workers
    finally:
        for connection in connections:
            connection.close()


def test_disconnect_revokes_an_already_authenticated_partial_post(companion):
    import socket
    import time

    server, edit = companion
    payload = json.dumps(dict(surface=str(id(server.window)), type="keydown", key="x")).encode()
    connection = socket.create_connection(("127.0.0.1", server.server.server_port), timeout=2)
    try:
        headers = (
            f"POST /input HTTP/1.1\r\nHost: {server.address}\r\nAuthorization: Bearer {server.token}\r\nContent-Length: {len(payload)}\r\n\r\n"
        ).encode()
        old_seen = server.last_seen
        connection.sendall(headers)
        for _ in range(100):
            if server.last_seen > old_seen:
                break
            time.sleep(0.005)
        assert server.last_seen > old_seen
        server.close()
        connection.sendall(payload)
        assert b"429" in connection.recv(2048).split(b"\r\n")[0]
        server.drain()
        assert edit.text() == "" and server.pending.empty()
    finally:
        connection.close()


def test_deeply_nested_json_rejected_without_handler_crash(companion):
    server, _ = companion
    payload = ("[" * 1100 + "]" * 1100).encode()
    request = urllib.request.Request(
        server.origin + "/input", data=payload, headers={"Authorization": "Bearer " + server.token}
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == 400


def test_context_menu_event_reaches_the_owned_widget(companion):
    from PySide6.QtCore import QObject, QEvent

    class Watch(QObject):
        def __init__(self):
            super().__init__()
            self.contexts = 0

        def eventFilter(self, obj, event):
            if event.type() == QEvent.ContextMenu:
                self.contexts += 1
                return True
            return False

    server, edit = companion
    watch = Watch()
    edit.installEventFilter(watch)
    point = edit.mapTo(server.window, edit.rect().center())
    server.dispatch(dict(surface=str(id(server.window)), type="context", x=point.x(), y=point.y()))
    assert watch.contexts == 1
