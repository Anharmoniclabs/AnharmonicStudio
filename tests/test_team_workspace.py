"""Controls remain reachable when production editors overflow a small display."""

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from mpclab.engine import Engine
from mpclab.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "start", lambda self: None)
    window = MainWindow(tmp_path, restore_session=False)
    window.show()
    QApplication.processEvents()
    yield window
    window._dirty = False
    window.close()


@pytest.mark.parametrize("width,height", [(800, 560), (1366, 768), (1920, 1000), (3840, 2160)])
def test_instrument_toolbar_controls_fit_and_remain_reachable(window, width, height):
    window.resize(width, height)
    window.studio.select(4)
    QApplication.processEvents()
    panel = window.studio.pages[4]
    for strip in (panel._native_head, panel._native_tools, panel.keyboard_toolbar):
        # Exercise the toolbar itself at a narrow editor width, even if
        # the outer editor scrolls to preserve its other controls' minimums.
        strip.setFixedWidth(320)
        QApplication.processEvents()
        assert strip.horizontalScrollBar().maximum() > 0
        for button in strip.widget().findChildren(QPushButton):
            strip.ensureWidgetVisible(button)
            QApplication.processEvents()
            viewport = strip.viewport()
            assert viewport.rect().contains(button.mapTo(viewport, button.rect().center()))
            assert button.height() <= viewport.height()


def test_instrument_keyboard_controls_and_accessible_values(window):
    window.studio.select(4)
    QApplication.processEvents()
    panel = window.studio.pages[4]
    panel.keyboard_toggle.click()
    assert panel.keyboard.isHidden()
    panel.keyboard_toggle.click()
    assert not panel.keyboard.isHidden()
    slider, attr, _lo, _hi, _log, value, _formatter = panel._controls[0]
    before = getattr(window.project.selected_patch, attr)
    slider.setValue(250 if slider.value() != 250 else 700)
    assert getattr(window.project.selected_patch, attr) != before
    assert slider.accessibleName() == "Brightness"
    assert slider.accessibleDescription() == value.text()
    assert panel.category.accessibleName() == "Instrument category"
    assert panel.track.accessibleName() == "Instrument mixer track"
    assert window.bpm_box.accessibleName() == "Project tempo in beats per minute"
    assert window.master_slider.accessibleName() == "Master output level"


def test_instrument_toolbar_adapts_to_larger_control_text(window):
    window.studio.select(4)
    strip = window.synth_panel.keyboard_toolbar
    strip.setFixedWidth(320)
    strip.widget().setStyleSheet("QPushButton { font-size: 20px; padding: 8px; }")
    QApplication.processEvents()
    for button in strip.widget().findChildren(QPushButton):
        strip.ensureWidgetVisible(button)
        QApplication.processEvents()
        assert button.height() <= strip.viewport().height()
        assert (
            strip.viewport().rect().contains(button.mapTo(strip.viewport(), button.rect().center()))
        )


def test_narrow_resize_reclaims_editor_space_but_stable_refresh_keeps_user_toggle(window):
    window.resize(800, 560)
    QApplication.processEvents()
    window.browser_frame.show()
    window._sync_responsive_panels()
    assert not window.browser_frame.isHidden()
    window.resize(760, 560)
    QApplication.processEvents()
    assert window.browser_frame.isHidden()
    window.toggle_browser()
    window._sync_responsive_panels()
    assert not window.browser_frame.isHidden()


def test_pad_selection_reveals_compact_rack_and_preserves_arrangement_focus(window):
    window.resize(800, 560)
    QApplication.processEvents()
    window.pad_side.hide()
    window.browser_frame.show()
    window.select_pad(5)
    QApplication.processEvents()
    assert window.pads.isVisible()
    assert window.pads.selected == 5
    assert window.browser_frame.isHidden()
    window.set_playlist_focus(True)
    window.select_pad(6)
    QApplication.processEvents()
    assert window.pad_side.isHidden()
    assert window.pads.selected == 6
