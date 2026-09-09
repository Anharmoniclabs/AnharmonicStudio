"""Qt needs a platform plugin before any widget module is imported.

Individual test modules set this too, so they still run under
`python -m unittest`. Doing it here as well means the choice does not depend
on which module pytest happens to import first.
"""

import os

# The developer desktop normally exports ``wayland;xcb``. Tests must override
# it, not merely provide a default, because CI and sandbox runs have no display.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Keep one strong reference for the full test session. Creating QApplication
# in an individual test and then letting Python collect the wrapper can destroy
# Qt's process-global application while later test modules still hold widgets,
# which manifests as a native abort instead of a useful assertion failure.
from PySide6.QtWidgets import QApplication  # noqa: E402

QT_APP = QApplication.instance() or QApplication([])


import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_user_settings(tmp_path):
    """Keep persisted window preferences out of the real desktop and other tests."""
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))


@pytest.fixture(autouse=True)
def release_test_windows():
    """Destroy only this offscreen test process's disposable Qt windows."""
    import gc
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    before = {id(widget) for widget in QT_APP.topLevelWidgets()}
    yield
    for widget in QT_APP.topLevelWidgets():
        if id(widget) not in before and isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
