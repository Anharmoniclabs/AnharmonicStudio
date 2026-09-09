"""Repeated offscreen window lifetimes release their engines and timers."""

import gc
import weakref

from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid

from mpclab.engine import Engine
from mpclab.ui import main_window
from scripts.render_studio_preview import PreviewSettings


def test_closed_workstations_release_native_widgets_and_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    for index in range(3):
        root = tmp_path / str(index)
        root.mkdir()
        window = main_window.MainWindow(root, restore_session=False)
        engine = weakref.ref(window.engine)
        window._dirty = False
        assert window.close()
        assert not window._ui_timer.isActive()
        assert not window._autosave_timer.isActive()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not isValid(window)
        del window
        gc.collect()
        assert engine() is None
