"""Non-owning references from editor panels back to their Qt workstation."""

import weakref
from PySide6.QtCore import QObject
from shiboken6 import isValid


class WindowClient:
    """Qt owns its children; children must not keep the entire window alive.

    Return the actual QObject (rather than a weak proxy) for Qt API calls.
    Non-QObject adapters used by standalone panels retain normal ownership.
    """

    @property
    def app(self):
        return self._window_ref() if self._window_ref is not None else self._standalone_app

    @app.setter
    def app(self, value):
        self._window_ref = weakref.ref(value) if isinstance(value, QObject) else None
        self._standalone_app = None if self._window_ref is not None else value


def emit_if_alive(owner, signal, *args):
    """A finishing worker may outlive the window that requested its result."""
    try:
        if isValid(owner):
            getattr(owner, signal).emit(*args)
    except RuntimeError:
        if isValid(owner):
            raise
