"""Music controls preserve audition ownership, undo and workspace visibility."""

from types import SimpleNamespace
import gc
import weakref

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget
from shiboken6 import isValid

from mpclab.engine import Engine
from mpclab.ui import main_window
from mpclab.ui import synth
from mpclab.ui.instruments import insert_pattern_instrument
from mpclab.ui.studio import StudioPanel
from tests.test_product_hardening_ui import window as workspace_window
from scripts.render_studio_preview import PreviewSettings

window = workspace_window


@pytest.fixture
def audition(window, monkeypatch):
    started, released, timers = [], [], []

    def start(note, velocity, *, instrument_id=None):
        token = object()
        started.append((note, instrument_id, token))
        return token

    monkeypatch.setattr(window.engine, "synth_preview_note_on", start)
    monkeypatch.setattr(window.engine, "synth_preview_note_off", released.append)
    monkeypatch.setattr(
        synth,
        "QTimer",
        SimpleNamespace(singleShot=lambda duration, parent, callback: timers.append(callback)),
    )
    return started, released, timers


def test_switching_instruments_releases_preview_and_old_timer_cannot_stop_new_preview(
    window, audition
):
    started, released, timers = audition
    panel = window.synth_panel
    first = insert_pattern_instrument(window, "native")
    panel.preview_sound()
    first_token = started[-1][2]
    second = insert_pattern_instrument(window, "native")
    assert first != second
    assert released == [first_token]
    assert panel._preview_note is None
    panel.preview_sound()
    second_token = started[-1][2]
    timers[0]()
    assert released == [first_token]
    assert panel._preview_note is not None
    timers[1]()
    assert released == [first_token, second_token]
    assert panel._preview_note is None


def test_replacing_preview_releases_only_captured_tokens_and_never_records(window, audition):
    started, released, timers = audition
    panel = window.synth_panel
    before = list(window.project.pattern().notes)
    window.btn_rec.setChecked(True)
    panel.preview_sound()
    panel.preview_sound()
    assert released == [started[0][2]]
    timers[0]()
    assert released == [started[0][2]]
    timers[1]()
    assert released == [entry[2] for entry in started]
    assert window.project.pattern().notes == before


def test_instrument_mixer_route_is_undoable_without_redundant_history(window):
    owner = insert_pattern_instrument(window, "native")
    panel = window.synth_panel
    before = window.project.instrument_patch(owner).track
    after = (before + 1) % len(window.project.tracks)
    history = len(window._undo)
    panel.track.setCurrentIndex(after)
    assert window.project.instrument_patch(owner).track == after
    assert len(window._undo) == history + 1
    panel._track_changed(after)
    panel._track_changed(-1)
    assert len(window._undo) == history + 1
    window.undo()
    assert window.project.instrument_patch(owner).track == before


@pytest.mark.parametrize("width", [480, 760, 1366, 1920])
def test_resize_keeps_current_workspace_fallback_navigation_visible(width):
    tabs = QTabWidget()
    for index in range(10):
        tabs.addTab(QLabel(str(index)), str(index))
    studio = StudioPanel(tabs)
    studio.activate(True)
    studio.quick_navigation.unavailable.emit("Exercise fallback navigation")
    studio.resize(1920, 700)
    studio.show()
    studio.select(3)
    QApplication.processEvents()
    studio.resize(width, 500)
    for _ in range(3):
        QApplication.processEvents()
    viewport = studio.mode_scroll.viewport()
    button = studio.buttons[3]
    assert viewport.rect().contains(button.mapTo(viewport, button.rect().center()))
    assert studio.more_button.isVisible()
    studio.close()
    studio.deleteLater()
    tabs.deleteLater()


def test_shown_resize_pending_navigation_timer_releases_closed_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    workstation = main_window.MainWindow(tmp_path, restore_session=False)
    engine = weakref.ref(workstation.engine)
    workstation.show()
    QApplication.processEvents()
    # Close with a coalesced navigation update pending. Closing a window must
    # release its engine without waiting for an unrelated future event loop.
    for width in (800, 1366, 760):
        workstation.resize(width, 600)
    assert workstation.studio._navigation_resize_timer.isActive()
    workstation.show_tab(workstation.TAB_SEQ)
    QTest.keyClick(workstation.step_grid, Qt.Key_End)
    assert workstation.step_grid._keyboard_reveal_timer.isActive()
    workstation._dirty = False
    assert workstation.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(workstation)
    del workstation
    gc.collect()
    assert engine() is None


def test_closing_with_real_instrument_audition_timer_releases_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    monkeypatch.setattr(main_window, "QSettings", PreviewSettings)
    workstation = main_window.MainWindow(tmp_path, restore_session=False)
    engine = weakref.ref(workstation.engine)
    workstation.show()
    QApplication.processEvents()
    workstation.synth_panel.preview_sound()
    assert workstation.synth_panel._preview_note is not None
    workstation._dirty = False
    assert workstation.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(workstation)
    del workstation
    gc.collect()
    assert engine() is None
